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
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ashare_stock_pipeline as sp


def pool_at(day: str, require_history_days: int = 0) -> dict:
    """F4五态: TRADEABLE/SUSPENDED_CONFIRMED/DATA_MISSING/NOT_YET_LISTED/DELISTED_CONFIRMED; 唯一代码去重。"""
    import ashare_calendar as cal
    if not cal.is_trading_day(day):  # fail-closed: 非交易日/UNKNOWN拒绝
        raise RuntimeError(f"非确认交易日: {day}")
    T = pd.Timestamp(day).normalize()
    buckets = {k: [] for k in ("TRADEABLE", "SUSPENDED_CONFIRMED", "DATA_MISSING", "NOT_YET_LISTED", "DELISTED_CONFIRMED")}
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
        df = pd.read_csv(p, usecols=["date"])
        d = pd.to_datetime(df["date"]).dt.normalize()
        first, last = d.min(), d.max()
        if T < first:
            buckets["NOT_YET_LISTED"].append(code)  # 首行情近似上市日
        elif T > last + pd.Timedelta(days=60):
            buckets["DELISTED_CONFIRMED"].append(code)  # 长期无行情(近似退市, 保守排除)
        elif (T > first) and ((T - first).days < require_history_days):
            buckets["NOT_YET_LISTED"].append(code)  # 上市历史不足
        elif T not in set(d):
            buckets["DATA_MISSING"].append(code)  # 当日无K线: 数据未到/漏行/停牌不可区分→不作停牌确认
        else:
            buckets["TRADEABLE"].append(code)
    return {"date": str(T.date()),
            "buckets": buckets,
            "stats": {k: len(v) for k, v in buckets.items()},
            "universe": len(seen), "sector_membership": len(sector_membership),
            "caveats": {"st_history": "UNAVAILABLE", "industry": "当前快照非历史时点",
                        "suspension": "无权威停复牌数据, 当日无K线一律DATA_MISSING不作SUSPENDED_CONFIRMED",
                        "survivorship": "仅当前配置池, 历史已退市/被替换股不在内"}}
