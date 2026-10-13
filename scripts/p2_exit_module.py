# -*- coding: utf-8 -*-
"""审查十五轮P2: 退出模块消融 — 入场冻结(V4+阶段1过滤), 仅变退出: X0现行(v4状态=0清仓) X1 MA50破位 X2 MA100破位
X3 高点回撤25%跟踪 X4 20日低点棘轮。指标: 终值/MDD/捕获中位。"""
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
from engine import run_specialist
import mu_af_v2 as M

c = M.c
f = build_features(c, [])
dd = f["dd252"]
entry_sig = ((M.v4 == 1) & rule_state(c).rolling(120, min_periods=1).max().astype(bool)).values
ma50, ma100 = c.rolling(50).mean(), c.rolling(100).mean()
vol20 = c.pct_change().rolling(20).std()
low20 = c.rolling(20).min()
n = len(c)


def positions(mode):
    pos = np.zeros(n)
    in_pos = False
    peak = np.nan
    for i in range(n):
        if not in_pos and entry_sig[i]:
            in_pos = True
            peak = c.iloc[i]
        if in_pos:
            peak = max(peak, c.iloc[i])
            x = c.iloc[i]
            if mode == "X0":
                dead = M.v4.iloc[i] == 0
            elif mode == "X1":
                dead = x < ma50.iloc[i]
            elif mode == "X2":
                dead = x < ma100.iloc[i]
            elif mode == "X3":
                dead = x < peak * 0.75
            else:
                dead = x < low20.iloc[i - 1]
            if dead:
                in_pos = False
            else:
                pos[i] = 1.0
    return pd.Series(pos, index=c.index)


print("入场冻结=V4+阶段1过滤 | 对照: X0现行528.18/-19.4%")
for mode in ("X0", "X1", "X2", "X3", "X4"):
    r = run_specialist(c, positions(mode), M.ext85, M.cal, budget=100.0)
    eq = r[0]
    print(f"{mode}: 终值={float(eq.iloc[-1]):.2f} 持仓日={int((positions(mode)>0).sum())} MDD={float((eq/eq.cummax()-1).min()):.1%}")
