# -*- coding: utf-8 -*-
"""A股交易日历 v3 (R9/E1-E2): 历史成交日历与官方未来交易日程分离 + 缓存完整性。

- 历史日历: data/etf_*.csv 成交日并集(回测对齐), 与官方日程交叉校验
- 未来日程: akshare.tool_trade_date_hist_sina 官方交易安排(含当年未来交易日) → forward缓存
- calendar_status: ≤历史末日→历史并集; >历史末日→官方日程; 双源皆无→UNKNOWN(fail-closed不放宽)
- 双源重叠区不一致→raise阻断; 官方休市日/周末双重门禁
- E1缓存完整性: _load每次stat源文件(size+mtime_ns)对比meta→变化即重建;
  解析异常(KeyError等)一律重建; 新增来源文件→重建; 重建失败→raise
"""
import hashlib
import json
import os
from glob import glob

import pandas as pd

CONFIRMED_OPEN, CONFIRMED_CLOSED, UNKNOWN = "CONFIRMED_OPEN", "CONFIRMED_CLOSED", "UNKNOWN"

# 已核实的官方休市日(交叉校验项; 非主数据源)
KNOWN_HOLIDAYS = {pd.Timestamp(x).date() for x in [
    "2026-01-01", "2026-01-02",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
]}
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_P = os.path.join(DATA_DIR, "ashare_calendar.csv")
META_P = os.path.join(DATA_DIR, "ashare_calendar.meta.json")
FWD_P = os.path.join(DATA_DIR, "ashare_calendar_forward.csv")
FWD_META_P = os.path.join(DATA_DIR, "ashare_calendar_forward.meta.json")
_CACHE = None      # 历史成交日 set[pd.Timestamp]
_FWD_CACHE = None  # 官方日程 set[pd.Timestamp]


def _sources() -> list:
    return sorted(glob(os.path.join(DATA_DIR, "etf_*.csv")))


def _file_hash(p: str) -> str:
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _file_sig(p: str) -> tuple:
    st = os.stat(p)
    return (st.st_size, st.st_mtime_ns)


def build_cache(force: bool = False) -> pd.DataFrame:
    """历史成交日并集(仅etf_*.csv)。周末=数据污染拒绝; force失效内存缓存。"""
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
    meta = {"generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "n_sources": len(files), "source_sha256": src_meta,
            "source_sigs": {os.path.basename(f): _file_sig(f) for f in files},
            "range": [str(days[0].date()), str(days[-1].date())], "n_days": len(days)}
    pd.DataFrame({"date": pd.to_datetime(days)}).to_csv(CACHE_P, index=False)
    with open(META_P, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, default=str)
    _CACHE = set(pd.to_datetime(days))
    return pd.DataFrame({"date": pd.to_datetime(days)})


def build_forward_cache(force: bool = False) -> pd.DataFrame:
    """E2: 官方交易日程(含未来) → forward缓存。与历史日历重叠区必须一致。"""
    global _FWD_CACHE
    if force:
        _FWD_CACHE = None
    import akshare as ak
    df = ak.tool_trade_date_hist_sina()
    days = sorted(set(pd.to_datetime(df["trade_date"]).dt.normalize()))
    bad = [x for x in days if x.weekday() >= 5]
    assert not bad, f"官方日程含周末(异常): {bad[:3]}"
    for h in KNOWN_HOLIDAYS:
        assert pd.Timestamp(h) not in days, f"官方日程含已核实休市日{h}—数据源异常"
    p, mp = FWD_P, FWD_META_P
    pd.DataFrame({"date": pd.to_datetime(days)}).to_csv(p, index=False)
    with open(mp, "w", encoding="utf-8") as f:
        json.dump({"generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
                   "range": [str(days[0].date()), str(days[-1].date())], "n_days": len(days),
                   "sha256": _file_hash(p)}, f, ensure_ascii=False, indent=1)
    _FWD_CACHE = set(pd.to_datetime(days))
    _cross_check()
    return pd.DataFrame({"date": pd.to_datetime(days)})


def _cross_check():
    """重叠区(≤历史末日∩≥历史首日): 成交并集⊆官方日程(成交日必为官方交易日)。不一致→阻断。"""
    if _CACHE and _FWD_CACHE:
        viol = sorted(x for x in _CACHE if x not in _FWD_CACHE)
        assert not viol, f"历史成交日不在官方交易日程中(双源冲突, 阻断): {viol[:5]}"


def _load() -> set:
    """E1: 缓存命中前stat源文件(size+mtime_ns)——底层更新→自动重建; 损坏/新增来源→重建; 失败raise。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        if os.path.exists(CACHE_P) and os.path.exists(META_P):
            with open(META_P, encoding="utf-8") as f:
                meta = json.load(f)
            sigs = meta.get("source_sigs", {})
            cur_files = {os.path.basename(f) for f in _sources()}
            if cur_files == set(sigs) and all(_file_sig(os.path.join(DATA_DIR, k)) == tuple(v) for k, v in sigs.items()):
                df = pd.read_csv(CACHE_P)
                _CACHE = set(pd.to_datetime(df["date"]).dt.normalize())
                return _CACHE
    except (json.JSONDecodeError, pd.errors.ParserError, KeyError, ValueError, OSError):
        pass  # 损坏→重建
    build_cache()
    return _CACHE


def _load_forward() -> set:
    global _FWD_CACHE
    if _FWD_CACHE is not None:
        return _FWD_CACHE
    try:
        if os.path.exists(FWD_P) and os.path.exists(FWD_META_P):
            with open(FWD_META_P, encoding="utf-8") as f:
                meta = json.load(f)
            if _file_hash(FWD_P) == meta.get("sha256"):
                df = pd.read_csv(FWD_P)
                _FWD_CACHE = set(pd.to_datetime(df["date"]).dt.normalize())
                return _FWD_CACHE
    except (json.JSONDecodeError, pd.errors.ParserError, KeyError, ValueError, OSError):
        pass
    build_forward_cache()
    return _FWD_CACHE


def calendar_status(d) -> str:
    """历史区用成交并集; 未来区用官方日程; 双源皆无→UNKNOWN(fail-closed)。"""
    x = pd.Timestamp(d).normalize()
    cal = _load()
    lo, hi = min(cal), max(cal)
    if lo <= x <= hi:
        return CONFIRMED_OPEN if x in cal else CONFIRMED_CLOSED
    fwd = _load_forward()
    if fwd:
        flo, fhi = min(fwd), max(fwd)
        if flo <= x <= fhi:
            return CONFIRMED_OPEN if x in fwd else CONFIRMED_CLOSED
    return UNKNOWN


def is_trading_day(d) -> bool:
    s = calendar_status(d)
    if s == UNKNOWN:
        raise RuntimeError(f"日历未确认(超出历史与官方日程覆盖): {pd.Timestamp(d).date()} — fail-closed拒绝")
    return s == CONFIRMED_OPEN


def _next_bdate(d) -> pd.Timestamp:
    x = pd.Timestamp(d).normalize() + pd.Timedelta(days=1)
    while True:
        s = calendar_status(x)
        if s == UNKNOWN:
            raise RuntimeError(f"下一交易日落在日历未确认范围: {x.date()} — fail-closed, 需更新官方日程")
        if s == CONFIRMED_OPEN:
            return x
        x += pd.Timedelta(days=1)


def validate_cross_check() -> dict:
    cal = _load()
    assert cal, "日历缓存为空"
    lo, hi = min(cal), max(cal)
    out = {"range": [str(lo.date()), str(hi.date())], "days": len(cal), "violations": []}
    for h in sorted(KNOWN_HOLIDAYS):
        if lo <= pd.Timestamp(h) <= hi and pd.Timestamp(h) in cal:
            out["violations"].append(str(h))
    assert not out["violations"], f"官方休市日出现在推导日历中: {out['violations']}"
    _cross_check()
    fwd = _load_forward()
    out["forward_range"] = [str(min(fwd).date()), str(max(fwd).date())] if fwd else None
    out["forward_days"] = len(fwd)
    return out
