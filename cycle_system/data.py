# -*- coding: utf-8 -*-
"""
数据层：行情 / 估值 / 商品数据获取与本地缓存。

数据源（绕过失效系统代理，直连）：
- 行业指数: 同花顺 stock_board_industry_index_ths (2010至今)
- 基准指数: 新浪 stock_zh_index_daily (沪深300)
- 个股日K: 新浪 stock_zh_a_daily (前复权, 全量)
- 个股PB:  百度股市通 stock_zh_valuation_baidu (近十年)
- 期货:    新浪 futures_main_sina (主力连续)
- 美股:    新浪 stock_us_daily (美光=存储周期代理)

统一规范列: date, open, high, low, close, volume, amount
"""
import os
import time
import datetime as dt

import pandas as pd
import numpy as np

import _path  # noqa: F401  (加入 .libs)
os.environ["NO_PROXY"] = "*"  # 绕过失效系统代理（本机 127.0.0.1:7892 已不可用）

import socket  # noqa: E402

# 全局网络超时兜底: 防止无 timeout 的第三方请求永久挂起
socket.setdefaulttimeout(30)

import requests  # noqa: E402

_orig_session_request = requests.Session.request


def _request_with_timeout(self, *args, **kwargs):
    kwargs.setdefault("timeout", 30)
    return _orig_session_request(self, *args, **kwargs)


requests.Session.request = _request_with_timeout

import akshare as ak  # noqa: E402

import config  # noqa: E402

CACHE = config.DATA_DIR
os.makedirs(CACHE, exist_ok=True)

_OHLC_MAP = {
    "日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
    "成交量": "volume", "成交额": "amount",
    "开盘价": "open", "收盘价": "close", "最高价": "high", "最低价": "low",
    "date": "date", "open": "open", "close": "close", "high": "high", "low": "low",
    "volume": "volume", "amount": "amount",
}
_KEEP = ["date", "open", "high", "low", "close", "volume", "amount"]


# ---------------- 基础工具 ----------------

def _norm_ohlc(df):
    if df is None or len(df) == 0:
        return None
    df = df.rename(columns=_OHLC_MAP)
    keep = [c for c in _KEEP if c in df.columns]
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in keep[1:]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date")
    df = df.drop_duplicates(subset="date").reset_index(drop=True)
    return df if len(df) > 0 else None


def _retry(fn, times=3, wait=2.0):
    last = None
    for i in range(times):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(wait * (i + 1))
    raise last


def _safe(fn, default=None, times=3, wait=2.0, label=""):
    try:
        return _retry(fn, times=times, wait=wait)
    except Exception as e:  # noqa: BLE001
        print(f"[data] 请求失败(已重试) {label}: {type(e).__name__} {str(e)[:100]}")
        return default


def _cache_path(kind, key):
    safe = str(key).replace("/", "_").replace("\\", "_").replace(":", "_").replace("*", "")
    return os.path.join(CACHE, f"{kind}_{safe}.csv")


def _load_cache(path):
    if os.path.exists(path) and os.path.getsize(path) > 10:
        try:
            return pd.read_csv(path, parse_dates=["date"])
        except Exception:
            return None
    return None


def _save_cache(path, df):
    try:
        df.to_csv(path, index=False)
    except Exception as e:  # noqa: BLE001
        print(f"[data] 缓存写入失败 {path}: {e}")


def _last_trade_day_like():
    d = pd.Timestamp.now().normalize()
    while d.weekday() >= 5:
        d -= pd.Timedelta(days=1)
    return d


def _is_fresh(df, max_lag_days=1):
    if df is None or len(df) == 0:
        return False
    ref = _last_trade_day_like() - pd.Timedelta(days=max_lag_days)
    return df["date"].max() >= ref


OFFLINE = False  # True = 只用本地缓存（哪怕过期），绝不访问网络


def _fetch_incremental(kind, key, fetch_fn, max_lag_days=1, label=""):
    """增量拉取（fetch_fn 支持起始日期参数）。"""
    path = _cache_path(kind, key)
    cached = _load_cache(path)
    if cached is not None and len(cached) > 0:
        if OFFLINE or _is_fresh(cached, max_lag_days):
            return cached
        last = cached["date"].max()
        start = (last + pd.Timedelta(days=1)).strftime("%Y%m%d")
        new = _safe(lambda: fetch_fn(start), label=label)
        if new is not None and len(new) > 0:
            df = pd.concat([cached, new], ignore_index=True)
            df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
            _save_cache(path, df)
            return df
        return cached
    if OFFLINE:
        return None
    df = _safe(lambda: fetch_fn(config.START_DATE), label=label)
    if df is not None and len(df) > 0:
        _save_cache(path, df)
    return df


def _fetch_full(kind, key, fetch_fn, max_lag_days=4, label=""):
    """全量拉取（数据源不支持日期区间时用；靠新鲜度容忍天数控制频率）。"""
    path = _cache_path(kind, key)
    cached = _load_cache(path)
    if cached is not None and len(cached) > 0 and (OFFLINE or _is_fresh(cached, max_lag_days)):
        return cached
    if OFFLINE:
        return cached
    df = _safe(fetch_fn, label=label)
    if df is not None and len(df) > 0:
        _save_cache(path, df)
        return df
    return cached


def get_etf_dividends(symbol):
    """ETF 分红历史（新浪: 日期 + 累计分红）。缓存 7 天, 失败返回 None。"""
    path = os.path.join(CACHE, f"etfdiv_{symbol}.csv")

    def _from_cache():
        if os.path.exists(path):
            try:
                df = pd.read_csv(path)
                if len(df) > 0 and {"date", "cum"} <= set(df.columns):
                    return df
            except Exception:
                return None
        return None

    if (not OFFLINE) and (not os.path.exists(path)
                          or time.time() - os.path.getmtime(path) > 7 * 86400):
        try:
            raw = ak.fund_etf_dividend_sina(symbol=symbol)
            if raw is not None and len(raw) > 0:
                df = pd.DataFrame({"date": pd.to_datetime(raw["日期"]),
                                   "cum": pd.to_numeric(raw["累计分红"], errors="coerce")})
                df = df.dropna().sort_values("date").reset_index(drop=True)
                if len(df):
                    df.to_csv(path, index=False)
                    return df
        except Exception:
            pass
    return _from_cache() if not OFFLINE else _from_cache()


def adjust_etf_prices(df, dividends=None):
    """修复新浪 ETF 未复权价格序列（返回修复后的副本）。

    1) 份额折算/合并: 行业 ETF/LOF 有 ±10% 涨跌停板, 单日 |收益|>12% 必为
       份额折算伪影 → factor = 当日收盘/前收, 之前全部价格 × factor
       （等比修复, 持有人价值不变; 实测 2021-2026 全 universe 共 6 起）
    2) 分红除息: 累计分红表差分得每次金额 amt, factor = 1 - amt/除息日前收,
       之前全部价格 × factor（近似全收益复权, 纯折算日 amt=0 自动跳过）

    volume/amount 不缩放（量不随份额折算变化, 折算后股数变化已由价格体现）。
    """
    if df is None or len(df) < 2:
        return df
    out = df.copy().reset_index(drop=True)
    px_cols = [c for c in ("open", "high", "low", "close") if c in out.columns]

    # 1) 份额折算/合并修复
    ret = out["close"].pct_change()
    for i in out.index[ret.abs() > 0.12]:
        f = float(out.loc[i, "close"]) / float(out.loc[i - 1, "close"])
        for c in px_cols:
            out.loc[: i - 1, c] = out.loc[: i - 1, c] * f

    # 2) 分红全收益复权（在折算修复后的序列上做）
    if dividends is not None and len(dividends):
        div = dividends.copy()
        div["amount"] = div["cum"].diff().fillna(div["cum"])
        dates = pd.to_datetime(out["date"])
        for _, row in div.iterrows():
            pos = int(dates.searchsorted(pd.to_datetime(row["date"])))
            amt = float(row["amount"]) if np.isfinite(row["amount"]) else 0.0
            if pos <= 0 or pos >= len(out) or amt <= 0:
                continue
            prev_close = float(out.loc[pos - 1, "close"])
            if prev_close <= 0:
                continue
            f = max(1.0 - amt / prev_close, 0.05)
            for c in px_cols:
                out.loc[: pos - 1, c] = out.loc[: pos - 1, c] * f
    return out


def get_etf_hist(symbol, adjusted=True):
    """行业 ETF 日线（新浪源, 全量返回; symbol 如 'sh515220'）。

    返回列: date/open/high/low/close/volume/amount（date 为 datetime64）。
    缓存 etf_<symbol>.csv, 新鲜度 1 天。
    adjusted=True 时先取原始缓存再做份额折算+分红复权修复（见 adjust_etf_prices）;
    新浪原始价格是未复权的, 2021-2026 间 universe 内有 6 起折算/合并伪影,
    不修复会引入单日 ±50~170% 的虚假收益。
    """
    def fetch():
        df = ak.fund_etf_hist_sina(symbol=symbol)
        if df is None or len(df) == 0:
            return None
        df = df.rename(columns=str.lower)
        df["date"] = pd.to_datetime(df["date"])
        keep = [c for c in ("date", "open", "high", "low", "close", "volume", "amount") if c in df.columns]
        return df[keep].sort_values("date").reset_index(drop=True)

    raw = _fetch_full("etf", symbol, fetch, max_lag_days=1, label=f"etf:{symbol}")
    if raw is None or not adjusted:
        return raw
    div = get_etf_dividends(symbol)
    return adjust_etf_prices(raw, div)


# ---------------- 同花顺行业指数 ----------------

def get_ths_industry_map(refresh=False):
    """同花顺行业名 -> 代码 映射（缓存1天）。"""
    path = os.path.join(CACHE, "ths_industry_map.csv")

    def _from_cache():
        if os.path.exists(path):
            try:
                df = pd.read_csv(path)
                return dict(zip(df["name"], df["code"].astype(str)))
            except Exception:
                return None
        return None

    if (not refresh) and os.path.exists(path) and (time.time() - os.path.getmtime(path)) < 86400:
        m = _from_cache()
        if m:
            return m
    if OFFLINE:
        return _from_cache()
    df = _safe(lambda: ak.stock_board_industry_name_ths(), label="ths行业列表")
    if df is not None and len(df) > 0:
        df.to_csv(path, index=False)
        return dict(zip(df["name"], df["code"].astype(str)))
    return _from_cache() or {}


def get_industry_hist(ths_name):
    """同花顺行业指数日K（显式按年分块抓取, 防整年静默缺口）。

    背景: akshare 内部按年翻页, 单年请求失败会静默跳过, 曾导致缓存出现
    整年缺口（2026-10 巡检发现 11/30 板块缺 1~4 年, 增量刷新只看末尾
    日期、中间缺口永不自愈）。本实现逐年抓取并拼接:
    - 年块失败重试 3 次后仍失败 → 打印警告（不再静默）
    - 最终序列存在 >40 自然日缺口（超出任何正常休市）→ 打印警告
    修复历史缺口的方法: 删除 data/ind_<板块>.csv 后重新抓取。
    """

    def fetch_year_chunk(year):
        for _attempt in range(3):
            try:
                raw = ak.stock_board_industry_index_ths(
                    symbol=ths_name, start_date=f"{year}0101", end_date=f"{year}1231")
                df = _norm_ohlc(raw)
                if df is not None and len(df) > 0:
                    return df
            except Exception:
                pass
        return None

    def fetch(start):
        y0 = int(str(start)[:4])
        y1 = min(int(str(config.END_DATE)[:4]), dt.date.today().year)
        frames, failed = [], []
        for y in range(y0, y1 + 1):
            chunk = fetch_year_chunk(y)
            if chunk is None:
                failed.append(y)
            else:
                frames.append(chunk)
        if failed:
            print(f"[data][警告] THS:{ths_name} 年份数据获取失败(存在缺口): {failed}")
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True)
        return df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)

    out = _fetch_incremental("ind", ths_name, fetch, max_lag_days=1, label=f"THS:{ths_name}")
    if out is not None and len(out) > 2:
        gaps = out["date"].diff().dt.days
        bad = out.loc[gaps.fillna(0) > 40, "date"]
        if len(bad):
            print(f"[data][警告] THS:{ths_name} 存在 >40 天缺口(疑似漏抓): {[str(d.date()) for d in bad]}")
    return out


# ---------------- 基准指数（沪深300, 新浪） ----------------

def get_benchmark():
    def fetch(_=None):
        raw = ak.stock_zh_index_daily(symbol="sh000300")
        return _norm_ohlc(raw)

    return _fetch_full("idx", "hs300", fetch, max_lag_days=1, label="沪深300")


# ---------------- 个股（新浪） ----------------

def _sina_symbol(code: str):
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    return None  # 北交所等暂不支持


def get_stock_hist(code):
    sym = _sina_symbol(code)
    if sym is None:
        return None

    def fetch(_=None):
        raw = ak.stock_zh_a_daily(symbol=sym, adjust="qfq")
        return _norm_ohlc(raw)

    return _fetch_full("stk", str(code).zfill(6), fetch, max_lag_days=4, label=f"stock:{code}")


# ---------------- 个股估值（百度PB/PE, 近十年） ----------------

def get_stock_valuation(code, indicator="市净率"):
    """返回 DataFrame(date, pb) 或 (date, pe)。"""
    kind = "pb" if "净" in indicator else "pe"
    path = _cache_path(kind, str(code).zfill(6))

    def fetch(_=None):
        raw = ak.stock_zh_valuation_baidu(symbol=str(code).zfill(6), indicator=indicator, period="近十年")
        if raw is None or len(raw) == 0:
            return None
        df = raw.copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df[indicator] = pd.to_numeric(df["value"], errors="coerce")
        df = df[["date", indicator]].dropna(subset=["date"])
        df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
        return df if len(df) > 0 else None

    return _fetch_full(kind, str(code).zfill(6), fetch, max_lag_days=7, label=f"{kind}:{code}")


# ---------------- 商品期货（新浪主力连续） ----------------

def get_futures_main(code):
    def fetch(start):
        raw = ak.futures_main_sina(symbol=code, start_date=start, end_date=config.END_DATE)
        return _norm_ohlc(raw)

    return _fetch_incremental("fut", code, fetch, max_lag_days=2, label=f"fut:{code}")


def attach_futures_columns(df, fut_code):
    """V2 期货背离出场: 给板块 df 附加 fut_mom20 / fut_below_ma60 列。

    无期货映射/数据不足时原样返回（cold_v2 自动退化为普通 cold）。
    """
    if not fut_code:
        return df
    try:
        fut = get_futures_main(fut_code)
    except Exception:
        return df
    if fut is None or len(fut) < 60:
        return df
    fc = fut.set_index(pd.to_datetime(fut["date"]))["close"].sort_index()
    fc = fc[~fc.index.duplicated()].reindex(df.index).ffill()
    df = df.copy()
    df["fut_mom20"] = fc.pct_change(20)
    df["fut_below_ma60"] = (fc < fc.rolling(60).mean()).astype(bool)
    return df


# ---------------- 美股（存储周期代理：美光MU） ----------------

def get_us_hist(symbol="MU"):
    def fetch(_=None):
        raw = ak.stock_us_daily(symbol=symbol, adjust="qfq")
        return _norm_ohlc(raw)

    return _fetch_full("us", symbol, fetch, max_lag_days=2, label=f"us:{symbol}")


# ---------------- 汇总加载 ----------------

def load_all():
    """加载全部周期板块数据：{板块名: {index, stocks:{code:df}, pb:{code:df}, fut, us}}"""
    sectors = config.CYCLE_SECTORS
    bench = get_benchmark()
    out = {"__benchmark__": bench}
    for i, (name, meta) in enumerate(sectors.items(), 1):
        print(f"[data] ({i}/{len(sectors)}) {name} ...")
        entry = {"meta": meta}
        entry["index"] = get_industry_hist(meta["ths"])
        time.sleep(0.5)
        # 代表股
        stocks = {}
        for code, cname in meta["stocks"][:config.BREADTH_STOCK_LIMIT]:
            df = get_stock_hist(code)
            if df is not None and len(df) > 250:
                stocks[code] = df
            time.sleep(0.4)
        entry["stocks"] = stocks
        # PB
        pbs = {}
        for code, cname in meta["stocks"][:config.PB_STOCK_LIMIT]:
            df = get_stock_valuation(code, "市净率")
            if df is not None and len(df) > 100:
                pbs[code] = df
            time.sleep(0.4)
        entry["pb"] = pbs
        # 期货代理
        if meta.get("fut"):
            entry["fut"] = get_futures_main(meta["fut"])
            time.sleep(0.4)
        # 美股代理（存储）
        if meta.get("us_proxy"):
            entry["us"] = get_us_hist(meta["us_proxy"])
            time.sleep(0.4)
        out[name] = entry
    return out


def sector_pb_series(pb_dict):
    """板块 PB = 代表股 PB 截面中位数。输入 {code: DataFrame(date, 市净率)}"""
    if not pb_dict:
        return None
    wide = None
    for code, df in pb_dict.items():
        col = "市净率" if "市净率" in df.columns else "pb"
        s = df.set_index("date")[col].rename(code)
        wide = s.to_frame(code) if wide is None else wide.join(s, how="outer")
    if wide is None or wide.shape[1] == 0:
        return None
    med = wide.median(axis=1)
    med.name = "pb"
    return med


def sector_stock_closes(stocks_dict):
    """个股收盘价宽表 (date × code)。"""
    if not stocks_dict:
        return None
    wide = None
    for code, df in stocks_dict.items():
        s = df.set_index("date")["close"].rename(code)
        wide = s.to_frame(code) if wide is None else wide.join(s, how="outer")
    return wide
