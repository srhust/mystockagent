# -*- coding: utf-8 -*-
"""审查十四轮: 复合拐点规则严格验证 — 阶段2信号 且 dd252≤-40% 且 收盘≤MA50×1.05。严格一对一[w-10,w+60]... 确认滞后: 覆盖=事件w∈[t,t+60]; P=含谷事件/总事件, R=覆盖谷/合格谷。MU+STX+WDC迁移检验。"""
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

for name, fn in {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv"}.items():
    c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20 = c.rolling(20).mean()
    ma50 = c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    s2 = ((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)).values
    events, last = [], -99
    for i in range(len(c)):
        if s2[i] and i - last >= 20:
            events.append(i)
            last = i
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
    used, cover, lags = set(), 0, []
    for w in events:
        ts = [t for t in qual if t <= w <= t + 60 and t not in used]
        if ts:
            t0 = max(ts)
            used.add(t0)
            cover += 1
            lags.append(w - t0)
    ev60 = pd.Series([c.shift(-60).iloc[w] / c.iloc[w] - 1 for w in events])
    print(f"{name}: 复合事件{len(events)} 合格谷{len(qual)} P={cover}/{len(events)} R={len(used)}/{len(qual)} "
          f"滞后中位{int(np.median(lags)) if lags else 'NA'}日 | 事件后60日中位{ev60.median():.1%} 胜率{(ev60>0).mean():.0%}" if len(events) else f"{name}: 无事件")
