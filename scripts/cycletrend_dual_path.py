# -*- coding: utf-8 -*-
"""审查十五轮P1c: CycleTrend双路径 — 路径A深跌复合确认(dd≤-40%+MA20收复上行+未脱MA50) vs 路径B浅回撤趋势重启
(MA50上拐10日 + 回撤10~30% + 去重120日, 独立入场理由)。MU九轮周期逐轮: 预警日/滞后/最大续跌。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features

c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "MU.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
f = build_features(c, [])
dd = f["dd252"]
ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
pathA = ((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)).values
pathB = ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)).values
evA, evB, la, lb = [], [], -99, -999
for i in range(len(c)):
    if pathA[i] and i - la >= 20:
        evA.append(i)
        la = i
    if pathB[i] and i - lb >= 120:
        evB.append(i)
        lb = i
hi_i = lo_i = 0
hi = lo = c.iloc[0]
trend, pivots = 0, []
for i in range(1, len(c)):
    x = c.iloc[i]
    if trend >= 0:
        if x > hi:
            hi, hi_i = x, i
        elif x <= hi * 0.70:
            pivots.append(("H", hi_i))
            trend, lo, lo_i = -1, x, i
    else:
        if x < lo:
            lo, lo_i = x, i
        elif x >= lo * 1.30:
            pivots.append(("L", lo_i))
            trend, hi, hi_i = 1, x, i
if trend < 0:
    pivots.append(("L", lo_i))
qual = [t for t in sorted(set(i for k, i in pivots if k == "L"))
        if len(c.iloc[t + 1:t + 121]) >= 60 and float(c.iloc[t + 1:t + 121].max() / c.iloc[t] - 1) >= 0.50]
print(f"路径A事件{len(evA)} 路径B事件{len(evB)} 合格谷{len(qual)}")
print("\n谷底日 | 路径A首次预警(滞后/续跌) | 路径B首次预警(滞后/续跌)")
covA = covB = 0
for t in qual:
    wA = min((w for w in evA if t <= w <= t + 60), default=None)
    wB = min((w for w in evB if t <= w <= t + 60), default=None)
    dd_after = lambda w: float(c.iloc[w:min(len(c), w + 60)].min() / c.iloc[w] - 1) if w is not None else None
    a = f"{(c.index[wA].date())} (+{wA - t}日, 续跌{dd_after(wA):.1%})" if wA is not None else "—"
    b = f"{(c.index[wB].date())} (+{wB - t}日, 续跌{dd_after(wB):.1%})" if wB is not None else "—"
    covA += wA is not None
    covB += wB is not None
    print(f"{c.index[t].date()} | {a} | {b}")
print(f"\n覆盖: A={covA}/9 B={covB}/9 联合={sum(1 for t in qual if any(t <= w <= t + 60 for w in evA) or any(t <= w <= t + 60 for w in evB))}/9")
