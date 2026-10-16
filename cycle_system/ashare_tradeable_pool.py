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
    """T日可投资池: {tradeable:[...], suspended:[...], not_yet_listed:[...], stats}。"""
    import ashare_calendar as cal
    if not cal.is_trading_day(day):  # fail-closed: 非交易日/UNKNOWN拒绝
        raise RuntimeError(f"非确认交易日: {day}")
    T = pd.Timestamp(day).normalize()
    tradeable, suspended, not_listed = [], [], []
    for code, name, sector in sp.configured_stocks():
        p = os.path.join(sp.DATA_DIR, f"{code}.csv")
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p, usecols=["date"])
        d = pd.to_datetime(df["date"]).dt.normalize()
        first, last = d.min(), d.max()
        if T < first:
            not_listed.append(code)
        elif T > last + pd.Timedelta(days=60):
            continue  # 已退市(池内无此情形, 防御)
        elif T > last:
            suspended.append(code)  # 最后行情早于T→停牌近似
        elif (T > first) and ((T - first).days < require_history_days):
            continue  # 上市历史不足
        elif T not in set(d):
            suspended.append(code)  # 当日无K线→停牌近似
        else:
            tradeable.append(code)
    return {"date": str(T.date()), "tradeable": tradeable, "suspended": suspended,
            "not_yet_listed": not_listed,
            "stats": {"tradeable": len(tradeable), "suspended": len(suspended),
                      "not_yet_listed": len(not_listed)},
            "caveats": {"st_history": "UNAVAILABLE", "industry": "当前快照非历史时点",
                        "suspension": "行情存在性近似, 非官方停复牌记录"}}
