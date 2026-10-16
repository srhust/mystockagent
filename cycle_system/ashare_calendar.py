# -*- coding: utf-8 -*-
"""A股交易日历 v2 (R8/D1-D3): 三态CONFIRMED_OPEN/CLOSED/UNKNOWN + fail-closed + 缓存身份。

数据源: data/etf_*.csv (仅A股市场证券行情; etfdiv分红数据不参与, 防日期污染)
- 交易日=市场成交日并集, 显式拒绝周末记录(数据污染门禁)
- 已核实官方休市日(2026国庆/元旦/春节)必须缺失, 违反即拒绝
- 缓存身份: data/ashare_calendar.meta.json 记录来源文件sha256/范围/生成时间;
  底层行情更新→哈希不一致→自动重建; 损坏→重建; 重建失败→raise(不静默)
- 覆盖范围外=UNKNOWN: 正式计分fail-closed拒绝, 不猜测
"""
import hashlib
import json
import os
from glob import glob

import pandas as pd

CONFIRMED_OPEN, CONFIRMED_CLOSED, UNKNOWN = "CONFIRMED_OPEN", "CONFIRMED_CLOSED", "UNKNOWN"

# 已核实的官方休市日(交叉校验项+范围外近似基准; 非主数据源)
KNOWN_HOLIDAYS = {pd.Timestamp(x).date() for x in [
    "2026-01-01", "2026-01-02",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23",  # 2026春节(上交所)
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
]}
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_P = os.path.join(DATA_DIR, "ashare_calendar.csv")
META_P = os.path.join(DATA_DIR, "ashare_calendar.meta.json")
_CACHE: set = None


def _sources() -> list:
    return sorted(glob(os.path.join(DATA_DIR, "etf_*.csv")))  # D2: 仅市场证券行情, 排除etfdiv分红


def _file_hash(p: str) -> str:
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build_cache(force: bool = False) -> pd.DataFrame:
    """从ETF行情日期并集推导交易日历。周末记录=数据污染→拒绝; force时失效内存缓存。"""
    global _CACHE
    if force:
        _CACHE = None
    files = _sources()
    assert files, f"无A股ETF行情文件: {DATA_DIR}"
    days, src_meta = set(), {}
    for f in files:
        df = pd.read_csv(f, usecols=["date"])
        d = pd.to_datetime(df["date"]).dt.normalize()
        bad = sorted({x.date() for x in d if x.weekday() >= 5})
        assert not bad, f"数据源污染: {os.path.basename(f)} 含周末记录: {bad[:3]}"
        days |= set(d)
        src_meta[os.path.basename(f)] = _file_hash(f)
    days = sorted(days)
    assert days, "推导日历为空"
    meta = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "n_sources": len(files), "source_sha256": src_meta,
        "range": [str(days[0].date()), str(days[-1].date())], "n_days": len(days),
    }
    pd.DataFrame({"date": pd.to_datetime(days)}).to_csv(CACHE_P, index=False)
    with open(META_P, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    _CACHE = set(pd.to_datetime(days))
    return pd.DataFrame({"date": pd.to_datetime(days)})


def _load() -> set:
    """加载缓存: 身份校验(底层文件哈希一致)→不一致/缺失/损坏自动重建; 失败raise(fail-closed)。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        if os.path.exists(CACHE_P) and os.path.exists(META_P):
            with open(META_P, encoding="utf-8") as f:
                meta = json.load(f)
            cur = {k: _file_hash(os.path.join(DATA_DIR, k)) for k in meta.get("source_sha256", {})}
            if cur == meta.get("source_sha256", {}):  # 底层未变→缓存有效
                df = pd.read_csv(CACHE_P)
                _CACHE = set(pd.to_datetime(df["date"]).dt.normalize())
                return _CACHE
    except (json.JSONDecodeError, pd.errors.ParserError, OSError):
        pass  # 损坏→走重建
    build_cache()  # 缺失/陈旧/损坏→重建(失败则异常上抛)
    return _CACHE


def calendar_status(d) -> str:
    """三态: 覆盖范围内CONFIRMED_OPEN/CLOSED; 范围外UNKNOWN(调用方必须fail-closed)。"""
    x = pd.Timestamp(d).normalize()
    cal = _load()
    lo, hi = min(cal), max(cal)
    if lo <= x <= hi:
        return CONFIRMED_OPEN if x in cal else CONFIRMED_CLOSED
    return UNKNOWN


def is_trading_day(d) -> bool:
    """仅CONFIRMED可信; UNKNOWN raise(fail-closed, 正式计分不得猜测)。"""
    s = calendar_status(d)
    if s == UNKNOWN:
        raise RuntimeError(f"日历未确认(超出缓存覆盖范围): {pd.Timestamp(d).date()} — fail-closed拒绝, 需更新行情数据后重建日历")
    return s == CONFIRMED_OPEN


def _next_bdate(d) -> pd.Timestamp:
    x = pd.Timestamp(d).normalize() + pd.Timedelta(days=1)
    while True:
        s = calendar_status(x)
        if s == UNKNOWN:
            raise RuntimeError(f"下一交易日落在日历未确认范围: {x.date()} — fail-closed, 需更新行情数据")
        if s == CONFIRMED_OPEN:
            return x
        x += pd.Timedelta(days=1)


def validate_cross_check() -> dict:
    """官方休市日必须从推导日历缺失 + 日历不得含周末 + 三态接口自检。"""
    cal = _load()
    assert cal, "日历缓存为空"
    lo, hi = min(cal), max(cal)
    out = {"range": [str(lo.date()), str(hi.date())], "days": len(cal), "violations": []}
    for h in sorted(KNOWN_HOLIDAYS):
        if lo <= pd.Timestamp(h) <= hi and pd.Timestamp(h) in cal:
            out["violations"].append(str(h))
    wk = [str(x.date()) for x in cal if x.weekday() >= 5]
    assert not out["violations"], f"官方休市日出现在推导日历中: {out['violations']}"
    assert not wk, f"推导日历含周末: {wk[:3]}"
    return out
