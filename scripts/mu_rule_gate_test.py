# -*- coding: utf-8 -*-
"""审查十二轮: 规则Nowcaster接入V4实验 — 买入信号门控(预警状态60日内才允许开仓), 对照V4基线。
零引擎改动: 只改信号序列; 口径=统一台账(费后/确认T+1)。"""
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
from bottom_nowcaster import build_features
import mu_af_v2 as M

c = M.c
f = build_features(c, [])
state = (f["dd252"] <= -0.35) & (f["pos1250"] <= 0.35)
state60 = state.rolling(60, min_periods=1).max().astype(bool)  # 预警后60日内保持开窗

buy = M.v4 == 1
gated = (M.v4 == 1) & state60  # 状态持仓过滤: 持仓需处于预警窗内(实验口径: 入场+持有双重过滤)
print(f"V4持仓日{int(buy.sum())}, 过滤后{int(gated.sum())}日(剔除{int(buy.sum()-gated.sum())}日)")

import copy
v4g = pd.Series(gated.astype(float), index=M.v4.index)
for tag, sig in [("V4基线", M.v4), ("V4+规则门控", v4g)]:
    r = run_specialist(M.c, sig, M.ext85, M.cal, budget=100.0)
    st = r[-1] if len(r) > 2 else r[1]
    try:
        eq = r[0]
        mdd = float((eq / eq.cummax() - 1).min())
        nt = len(r[1]) if hasattr(r[1], "__len__") else -1
        print(f"{tag}: 终值={float(eq.iloc[-1]):.2f} trades={nt} MDD={mdd:.1%}")
    except Exception:
        print(tag, repr(st)[:400])





