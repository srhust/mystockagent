# -*- coding: utf-8 -*-
"""审查十五轮P1收尾②: 双路径账户级检验 — 信号=A∪B事件后持120日(事件驱动持仓), 对照V4与V4+阶段1过滤。"""
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
ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
pathA = ((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)).values
pathB = ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)).values
ev = pd.Series((pathA | pathB).astype(float), index=c.index)
for label, sig in [("双路径∪持120日", ev.rolling(120, min_periods=1).max()),
                   ("V4+阶段1过滤(对照)", (M.v4 == 1) & rule_state(c).rolling(120, min_periods=1).max().astype(bool)),
                   ("V4+阶段1∪双路径120日", (M.v4 == 1) | ev.rolling(120, min_periods=1).max().astype(bool))]:
    sig2 = sig.astype(float) if hasattr(sig, "astype") else sig
    r = run_specialist(c, sig2, M.ext85, M.cal, budget=100.0)
    eq = r[0]
    print(f"{label}: 终值={float(eq.iloc[-1]):.2f} 笔数={len(r[1])} MDD={float((eq/eq.cummax()-1).min()):.1%}")
