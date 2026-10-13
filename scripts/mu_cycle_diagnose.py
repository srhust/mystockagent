# -*- coding: utf-8 -*-
"""审查十四轮: 回答"MU两年一周期很明显, 为什么系统捕捉不到" — 逐谷诊断dd252/pos1250在谷底处的取值。
若pos1250是元凶(周期底逐轮抬升导致5年位置不再低), 则测dd252单条件规则的严格口径。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features, rule_state

df = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "MU.csv"), parse_dates=["date"]).sort_values("date").set_index("date")
c = df["close"].astype(float)
f = build_features(c, [])
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
print("=== 逐谷诊断: 谷底日与谷前60日内 dd252最深处 / pos1250(5年位置) ===")
for t in qual:
    w0 = max(0, t - 60)
    dd_min = float(f["dd252"].iloc[w0:t + 1].min())
    dd_at = float(f["dd252"].iloc[t])
    pos_min = float(f["pos1250"].iloc[w0:t + 1].min())
    pos_at = float(f["pos1250"].iloc[t])
    gap = (c.index[t] - c.index[qual[qual.index(t) - 1]]).days / 365.25 if qual.index(t) > 0 else np.nan
    flag_dd = "深跌✓" if dd_min <= -0.35 else "深跌✗"
    flag_pos = "低位✓" if pos_min <= 0.35 else "低位✗"
    print(f"{c.index[t].date()} 间隔{gap:.1f}年 | dd252谷前最浅取{dd_min:.1%}(当日{dd_at:.1%}){flag_dd} | pos1250最低{pos_min:.2f}(当日{pos_at:.2f}){flag_pos} | 谷价{c.iloc[t]:.0f}")
# dd252单条件规则严格评价
print("\n=== dd252单条件(dd≤-35%)严格评价: 连续5日确认, 一对一[w,w+120], P@60/R@60 ===")
sig = (f["dd252"] <= -0.35).values
events, run, last_ev = [], 0, -99
for i in range(len(sig)):
    run = run + 1 if sig[i] else 0
    if run == 5 and i - last_ev >= 20:
        events.append(i)
        last_ev = i
used, p60, dd60 = set(), 0, []
for w in events:
    ts = [t for t in qual if w <= t <= w + 120 and t not in used]
    if ts:
        t0 = min(ts)
        used.add(t0)
        if t0 <= w + 60:
            p60 += 1
        dd60.append(float(c.iloc[w:min(len(c), w + 60)].min() / c.iloc[w] - 1))
rec60 = sum(1 for t in qual if any(w <= t <= w + 60 for w in events))
print(f"事件{len(events)} 合格谷{len(qual)} 命中{len(used)} P@60={p60}/{len(events)} R@60={rec60}/{len(qual)} 续跌中位{np.median(dd60):.1%}" if dd60 else f"事件{len(events)} 无命中")
