# -*- coding: utf-8 -*-
"""历史可投资池 (R9/E3): 任意历史T日实际可选、可交易的个股集合。

判定口径(基于已落盘249只逐日行情):
- 上市: T >= 该股首行情日(近似上市日, 如实标注)
- 未退市: T <= 最后行情日+60日容差(池内现役股; 历史已退市股不在配置池, 如实标注)
- 未停牌: T 当日存在K线(行情存在性近似; 真实停复牌逐日记录UNAVAILABLE)
- ST历史: UNAVAILABLE(需专门历史接口, 不猜测)
- 行业归属: 当前config快照(非历史时点行业, 如实标注)
"""
import json
import io
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ashare_stock_pipeline as sp


_CODE_META = {}  # R21perf: {code: (first, last, dates_set, factor_cov)} 249CSV只读一次
_POOL_AT_CACHE = {}  # R21: 纯缓存(同day同require_history_days同结果, 不改判定口径; 进程内)


def pool_at(day: str, require_history_days: int = 0) -> dict:
    """F4五态: TRADEABLE/SUSPENDED_CONFIRMED/DATA_MISSING/NOT_YET_LISTED/DELISTED_CONFIRMED; 唯一代码去重。"""
    ck = (str(day), require_history_days)
    if ck in _POOL_AT_CACHE:  # R21: 回测热点优化(每日入场模式每日调用249CSV)
        return _POOL_AT_CACHE[ck]
    import ashare_calendar as cal
    if not cal.is_trading_day(day):  # fail-closed: 非交易日/UNKNOWN拒绝
        raise RuntimeError(f"非确认交易日: {day}")
    T = pd.Timestamp(day).normalize()
    buckets = {k: [] for k in ("TRADEABLE", "SUSPENDED_CONFIRMED", "DATA_MISSING", "NOT_YET_LISTED", "INACTIVE_UNVERIFIED", "INSUFFICIENT_HISTORY", "DELISTED_CONFIRMED", "DATA_UNVERIFIED")}
    seen, sector_membership = set(), []
    for code, name, sector in sp.configured_stocks():  # F4: 249唯一代码去重, 251条行业关联单列
        sector_membership.append({"code": code, "sector": sector})
        if code in seen:
            continue
        seen.add(code)
        p = os.path.join(sp.DATA_DIR, f"{code}.csv")
        if not os.path.exists(p):
            buckets["DATA_MISSING"].append(code)  # 文件缺失≠可交易名单, 保守排除
            continue
        head = io.open(p, encoding="utf-8").readline()  # H3b: 数据质量门禁——未过raw/factor迁移不进TRADEABLE
        if "adj_factor" not in head:
            buckets["DATA_UNVERIFIED"].append(code)
            continue
        if code in _CODE_META:  # R21perf: per-code元数据进程级缓存(249CSV读一次, 而非每日重读)
            first, last, dset, fcov = _CODE_META[code]
        else:
            df = pd.read_csv(p, usecols=["date", "adj_factor"])
            fcov = float(df["adj_factor"].notna().mean() if len(df) else 0.0)
            d = pd.to_datetime(df["date"]).dt.normalize()
            first, last, dset = d.min(), d.max(), set(d)
            _CODE_META[code] = (first, last, dset, fcov)
        if fcov < 0.999:
            buckets["DATA_UNVERIFIED"].append(code)  # H3b: 表头有列但因子实际缺失→不进TRADEABLE
            continue
        if T < first:
            buckets["NOT_YET_LISTED"].append(code)  # 仅首行情晚于T(首行情近似上市日)
        elif code in sp.DELISTED_CONFIRMED and T > pd.Timestamp(sp.DELISTED_CONFIRMED[code]["last_trade_date"] if isinstance(sp.DELISTED_CONFIRMED[code], dict) else sp.DELISTED_CONFIRMED[code]):
            buckets["DELISTED_CONFIRMED"].append(code)  # H2: 按最后交易日(可交易性终点)
        elif T > last + pd.Timedelta(days=60):
            buckets["INACTIVE_UNVERIFIED"].append(code)  # 无权威记录不称CONFIRMED
        elif (T > first) and ((T - first).days < require_history_days):
            buckets["INSUFFICIENT_HISTORY"].append(code)  # G2: 已上市但数据不足, 与上市状态分离
        elif T not in dset:
            buckets["DATA_MISSING"].append(code)  # 当日无K线: 数据未到/漏行/停牌不可区分→不作停牌确认
        else:
            buckets["TRADEABLE"].append(code)
    out = {"date": str(T.date()),
           "buckets": buckets,
           "stats": {k: len(v) for k, v in buckets.items()},
           "universe": len(seen), "sector_membership": len(sector_membership),
           "caveats": {"st_history": "UNAVAILABLE", "industry": "当前快照非历史时点",
                       "suspension": "无权威停复牌数据, 当日无K线一律DATA_MISSING不作SUSPENDED_CONFIRMED",
                       "survivorship": "仅当前配置池, 历史已退市/被替换股不在内",
                       "inactive": "INACTIVE_UNVERIFIED=长期无行情待权威退市记录核实(603603/000961类)",
                       "fetch_start": "行情抓取默认2020起, 更早日期的上市判定不可靠"}}
    _POOL_AT_CACHE[ck] = out  # R21: 纯缓存写入(同参同结果)
    return out
