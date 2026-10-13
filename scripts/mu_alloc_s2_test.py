# -*- coding: utf-8 -*-
"""审查十四轮①: 分配器式置信实验 — 阶段2确认作为"放宽阶段1过滤"的置信输入(深跌区外, 有新鲜复苏确认则允许持仓)。
对照: V4基线 / 阶段1过滤(前优) / 阶段1∪阶段2(置信放宽)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import pandas as pd

from engine import run_specialist
from bottom_nowcaster import rule_state
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


c = M.c
st = rule_state(c)
st120 = st.rolling(120, min_periods=1).max().astype(bool)
ev = stage2_events(c, st)
ev_ser = pd.Series(0.0, index=c.index)
ev_ser.iloc[ev] = 1.0
ev120 = ev_ser.rolling(120, min_periods=1).max().astype(bool)
v4 = M.v4
variants = [
    ("V4基线", v4 == 1),
    ("V4+阶段1过滤", (v4 == 1) & st120),
    ("V4+阶段1∪阶段2置信放宽", (v4 == 1) & (st120 | ev120)),
]
for tag, sig in variants:
    sig2 = pd.Series(sig.astype(float), index=v4.index)
    r = run_specialist(M.c, sig2, M.ext85, M.cal, budget=100.0)
    eq = r[0]
    mdd = float((eq / eq.cummax() - 1).min())
    nt = len(r[1]) if hasattr(r[1], "__len__") else -1
    print(f"{tag}: 终值={float(eq.iloc[-1]):.2f} 笔数={nt} MDD={mdd:.1%} 持仓日={int((sig2>0).sum())}")
