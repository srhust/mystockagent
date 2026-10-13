# -*- coding: utf-8 -*-
"""审查十四轮: ③阶段2事件质量(事件后60日收益分布, 真/假确认对照) + ①接入V4(持仓需近120日内有阶段2确认) ②对照两版过滤。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from engine import run_specialist
from bottom_nowcaster import build_features, rule_state
import mu_af_v2 as M


def stage2_events(c, st):
    st250 = st.rolling(250, min_periods=1).max().astype(bool)
    ma20 = c.rolling(20).mean()
    cond = (c > ma20) & (ma20 > ma20.shift(5)) & st250
    events, last = [], -99
    for i in range(len(c)):
        if bool(cond.iloc[i]) and i - last >= 20:
            events.append(i)
            last = i
    return events


# ---------- ③ 事件质量(重点MU) ----------
c = M.c
st = rule_state(c)
events = stage2_events(c, st)
fwd60 = c.shift(-60) / c - 1
fwd20 = c.shift(-20) / c - 1
# MU合格谷底(与严格评价器同源)
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
used = set()
tag = []
for w in events:
    ts = [t for t in qual if t <= w <= t + 60 and t not in used]
    if ts:
        used.add(max(ts))
        tag.append(True)
    else:
        tag.append(False)
ev60 = pd.Series([fwd60.iloc[w] for w in events], index=events)
ev20 = pd.Series([fwd20.iloc[w] for w in events], index=events)
print(f"MU阶段2事件{len(events)}个(真确认{sum(tag)} 假{len(events)-sum(tag)})")
print(f"全样本无条件: 60日前瞻中位{fwd60.median():.1%} 20日{fwd20.median():.1%}")
print(f"事件后60日: 中位{ev60.median():.1%} 均{ev60.mean():.1%} 胜率{(ev60>0).mean():.0%} | 20日: 中位{ev20.median():.1%} 胜率{(ev20>0).mean():.0%}")
t60 = ev60[[i for i, t in zip(events, tag) if t]]
f60 = ev60[[i for i, t in zip(events, tag) if not t]]
print(f"真确认(n={len(t60)}): 60日中位{t60.median():.1%} | 假确认(n={len(f60)}): 60日中位{f60.median():.1%}")

# ---------- ① 接入V4: 持仓需近120日内有阶段2确认 ----------
ev_ser = pd.Series(0.0, index=c.index)
ev_ser.iloc[events] = 1.0
ev120 = ev_ser.rolling(120, min_periods=1).max().astype(bool)
v4 = M.v4
v4_s2 = (v4 == 1) & ev120
st120 = st.rolling(120, min_periods=1).max().astype(bool)
v4_s1 = (v4 == 1) & st120
for tag2, sig in [("V4基线", v4), ("V4+阶段1状态过滤", v4_s1), ("V4+阶段2确认过滤", v4_s2)]:
    sig2 = pd.Series(sig.astype(float), index=v4.index)
    r = run_specialist(M.c, sig2, M.ext85, M.cal, budget=100.0)
    eq = r[0]
    mdd = float((eq / eq.cummax() - 1).min())
    nt = len(r[1]) if hasattr(r[1], "__len__") else -1
    print(f"{tag2}: 终值={float(eq.iloc[-1]):.2f} 笔数={nt} MDD={mdd:.1%} 持仓日={int((sig2>0).sum())}")
