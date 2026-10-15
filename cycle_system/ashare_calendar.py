# -*- coding: utf-8 -*-
"""A股交易日历: 由本地A股ETF行情日期并集推导(市场实际成交日=交易日), 与已知节假日交叉校验。

数据源: data/etf_*.csv, data/etfdiv_*.csv (全部A股市场标的, 28+只)
- 推导日历缓存: data/ashare_calendar.csv (幂等重建)
- fallback: 无缓存时回退手工节假日表+周末(标注approx=True)
- Release Gate 阻断项: 前向计分必须使用推导日历(calib来源可审计)
"""
import os
from glob import glob

import pandas as pd

# 已核实的官方休市日(交叉校验项, 非主数据源)
KNOWN_HOLIDAYS = {pd.Timestamp(x).date() for x in [
    "2026-01-01", "2026-01-02",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
]}
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_P = os.path.join(DATA_DIR, "ashare_calendar.csv")
_CACHE: set = None


def build_cache(force: bool = False) -> pd.DataFrame:
    """从全部A股ETF CSV日期并集推导交易日历(市场成交日), 幂等。"""
    files = glob(os.path.join(DATA_DIR, "etf_*.csv")) + glob(os.path.join(DATA_DIR, "etfdiv_*.csv"))
    assert files, f"无A股ETF行情文件: {DATA_DIR}"
    days = set()
    for f in files:
        df = pd.read_csv(f, usecols=["date"])
        days |= set(pd.to_datetime(df["date"]).dt.normalize())
    days = sorted(days)
    cal = pd.DataFrame({"date": pd.to_datetime(days)})
    cal.to_csv(CACHE_P, index=False)
    return cal


def _load() -> set:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if os.path.exists(CACHE_P):
        df = pd.read_csv(CACHE_P)
        _CACHE = set(pd.to_datetime(df["date"]).dt.normalize())
    else:
        build_cache()
        _CACHE = set(pd.to_datetime(pd.read_csv(CACHE_P)["date"]).dt.normalize())
    return _CACHE


def is_trading_day(d) -> bool:
    """推导日历为主; 日期超出日历覆盖范围时回退周末+已知节假日(approx)。"""
    x = pd.Timestamp(d).normalize()
    cal = _load()
    if cal:
        lo, hi = min(cal), max(cal)
        if lo <= x <= hi:
            return x in cal
    return x.weekday() < 5 and x.date() not in KNOWN_HOLIDAYS  # approx范围外


def _next_bdate(d) -> pd.Timestamp:
    x = pd.Timestamp(d).normalize() + pd.Timedelta(days=1)
    while not is_trading_day(x):
        x += pd.Timedelta(days=1)
    return x


def validate_cross_check() -> dict:
    """已知官方节假日必须从推导日历中缺失(交叉校验); 返回审计dict。"""
    cal = _load()
    assert cal, "日历缓存为空"
    lo, hi = min(cal), max(cal)
    out = {"range": [str(lo.date()), str(hi.date())], "days": len(cal), "violations": []}
    for h in sorted(KNOWN_HOLIDAYS):
        if lo <= pd.Timestamp(h) <= hi and pd.Timestamp(h) in cal:
            out["violations"].append(str(h))
    assert not out["violations"], f"官方休市日出现在推导日历中(数据源污染): {out['violations']}"
    return out
