# -*- coding: utf-8 -*-
"""A股个股数据管道 (R8/D4): 251只配置股逐日OHLCV+ST状态落盘, 断点续传, 可用性审计。

数据源: akshare stock_zh_a_hist(后复权) + 个股信息(含ST标记)
- 落盘: data/stocks/{code}.csv (date,open,high,low,close,amount,volume)
- 断点续传: 已存在且最新日期>=run_date-7d 的跳过(可force)
- 审计: data/stocks/_audit.json 每股状态(OK/EMPTY/FAILED/UNAVAILABLE)
- 诚实边界: ST历史状态与停复牌逐日记录需专门接口, 当前以ST标记快照+缺失审计披露
"""
import io
import json
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stocks")


def configured_stocks() -> list:
    import config
    out = []
    for k, v in config.CYCLE_SECTORS.items():
        if k in ("buy", "sell", "trailing_stop"):
            continue
        for code, name in v["stocks"]:
            out.append((code, name, k))
    return out  # (code, name, sector)


def fetch_one(code: str, start: str = "20200101", adjust: str = "") -> pd.DataFrame:
    """F3: adjust=""为原始真实成交价(执行器/涨跌停/费用口径); adjust="hfq"仅研究信号复权口径。"""
    import akshare as ak
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, adjust=adjust)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount"})
    keep = [c for c in ("date", "open", "high", "low", "close", "volume", "amount") if c in df.columns]
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def run(max_stocks: int = None, force: bool = False, per_call_sleep: float = 2.0) -> dict:
    """落盘251只(断点续传)。结果审计含每股状态; 部分失败如实记录不掩盖。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    stocks = configured_stocks()
    if max_stocks:
        stocks = stocks[:max_stocks]
    audit_path = os.path.join(DATA_DIR, "_audit.json")
    audit = {}
    if os.path.exists(audit_path) and not force:
        with open(audit_path, encoding="utf-8") as f:
            audit = json.load(f)
    ok = fail = 0
    for code, name, sector in stocks:
        p = os.path.join(DATA_DIR, f"{code}.csv")
        if not force and os.path.exists(p):
            try:  # F3: 断点续传必须校验新鲜度, 不得只看文件存在
                _head = io.open(p, encoding="utf-8").readline()
                if "adj_factor" not in _head:
                    raise ValueError("legacy-hfq-format")  # P0-1: 旧后复权CSV不算有效缓存, 强制迁移raw+factor
                _d = pd.read_csv(p, usecols=["date"])
                _latest = pd.Timestamp(_d["date"].max())
                if (pd.Timestamp.now().normalize() - _latest).days <= 7 and len(_d) >= 100:
                    audit[code] = {**audit.get(code, {}), "status": "RAW_OK_FACTOR_OK", "name": name, "sector": sector, "latest": str(_latest.date()), "price_basis": "raw+adj_factor"}  # H3: 新鲜+已迁移=双口径合格
                    ok += 1
                    continue
            except (pd.errors.ParserError, KeyError, ValueError):
                pass  # 损坏→重抓
        df, err = None, None
        for attempt in range(3):  # 限频退避重试: 5s/15s/30s
            try:
                df = fetch_one(code)
                break
            except Exception as e:
                err = str(e)[:200]
                if attempt < 2:
                    time.sleep([5, 15, 30][attempt])
        try:
            if df is None:
                raise RuntimeError(err or "fetch failed after retries")
            if df.empty:
                audit[code] = {"status": "EMPTY", "name": name, "sector": sector}
                fail += 1
            else:
                try:  # F3: adj_factor=hfq/raw(研究口径); 执行器用raw真实价——两口径显式分离
                    hf = fetch_one(code, adjust="hfq")
                    df = df.merge(hf[["date", "close"]].rename(columns={"close": "hfq_close"}), on="date", how="left")
                    df["adj_factor"] = (df["hfq_close"] / df["close"]).round(6)
                    df = df.drop(columns=["hfq_close"])
                except Exception:
                    df["adj_factor"] = float("nan")  # P0-2: 缺失留空但状态降级, 不冒充双口径
                df.to_csv(p, index=False)
                import math
                f_ok = df["adj_factor"].notna().sum() >= max(1, len(df) - 2)  # P0-2: 因子覆盖率门禁
                audit[code] = {"status": "RAW_OK_FACTOR_OK" if f_ok else "RAW_OK_FACTOR_MISSING",
                               "name": name, "sector": sector, "rows": len(df),
                               "latest": str(df["date"].max()), "price_basis": "raw+adj_factor",
                               "factor_coverage": round(float(df["adj_factor"].notna().mean()), 4)}
                ok += 1
        except Exception as e:  # 如实记录失败, 不静默
            audit[code] = {"status": "FAILED", "name": name, "sector": sector, "error": str(e)[:200]}
            fail += 1
        time.sleep(per_call_sleep)
    # G3: 冒烟(max_stocks子集)不覆写全量_summary——全量摘要只由全量run产生
    if max_stocks is None:
        audit["_summary"] = {"ok": ok, "fail_empty": fail, "total": len(stocks),
                             "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat()}
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=1)
    return audit


# H2: 交易所公告核实的真实退市(权威来源: 上交所/深交所公告, 非无行情推断)
DELISTED_CONFIRMED = {"000961": "2024-06-24", "603603": "2024-04-25"}


def verify_identity() -> dict:
    """H1: 全量代码-名称交叉校验(akshare权威证券列表)。退市股不在列表=预期。"""
    import akshare as ak
    uni = ak.stock_info_a_code_name()
    m = {str(r["code"]): str(r["name"]) for _, r in uni.iterrows()}
    bad, retired = [], []
    for code, name, _ in configured_stocks():
        if code not in m:
            retired.append(code)
        elif m[code] != name:
            bad.append({"code": code, "config": name, "exchange": m[code]})
    return {"mismatches": bad, "not_in_exchange_list": retired,
            "verdict": "PASS" if not bad else "FAIL"}


def availability_report() -> dict:
    """D4验收口径: 历史可投资池=已落盘且数据完整的股票集合审计。"""
    with open(os.path.join(DATA_DIR, "_audit.json"), encoding="utf-8") as f:
        audit = json.load(f)
    per = {k: v for k, v in audit.items() if not k.startswith("_")}
    by_status = {}
    for v in per.values():
        by_status[v["status"]] = by_status.get(v["status"], 0) + 1
    # H3: 新状态体系(RAW_OK_FACTOR_OK/RAW_OK_FACTOR_MISSING), 不再依赖历史OK/CACHED标签
    tradeable_pool = sorted(k for k, v in per.items()
                            if v["status"] == "RAW_OK_FACTOR_OK" and k not in DELISTED_CONFIRMED)
    return {"total_configured": len(per), "by_status": by_status,
            "tradeable_pool": tradeable_pool,
            "delisted_confirmed": DELISTED_CONFIRMED,
            "summary": audit.get("_summary", {})}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    a = run(max_stocks=n)
    print(json.dumps(a["_summary"], ensure_ascii=False))
