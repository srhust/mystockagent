# -*- coding: utf-8 -*-
"""审查十四轮: 用户方案的严格实现 — 企稳信号(阶段2: MA20收复+上行, 需深跌区250日内)才建仓, 止损=信号日20日最低收盘,
跌破止损T+1清仓, 等下一个企稳信号再进。T日确认T+1成交, 费0.15%。对照V4。"""
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
import mu_af_v2 as M

c = M.c
f = build_features(c, [])
dd = f["dd252"]
ma20 = c.rolling(20).mean()
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
s2 = ((c > ma20) & (ma20 > ma20.shift(5)) & state250).values
n = len(c)
cost = 0.0015
cash, shares = 100.0, 0.0
in_pos = False
stop_px = np.nan
pend = None  # ('buy'|'sell', 日)
equity = pd.Series(np.nan, index=c.index)
trades = []
last_sig = -99
for i in range(1, n):
    px = c.iloc[i]
    if pend is not None:
        kind, d = pend
        pdx = c.iloc[d]
        if kind == "buy":
            shares = cash * (1 - cost) / pdx
            stop_px = float(c.iloc[max(0, d - 19):d + 1].min())
            trades.append((c.index[d], "BUY", pdx))
            cash = 0.0
            in_pos = True
        else:
            cash = shares * pdx * (1 - cost)
            trades.append((c.index[d], "SELL", pdx))
            shares = 0.0
            in_pos = False
        pend = None
    # T日决策
    if s2[i] and i - last_sig >= 20 and not in_pos and pend is None:
        last_sig = i
        pend = ("buy", i + 1)
    elif in_pos and not np.isnan(stop_px) and px < stop_px and pend is None:
        pend = ("sell", i + 1)
    equity.iloc[i] = cash + shares * px
eq = equity.dropna()
mdd = float((eq / eq.cummax() - 1).min())
nb = sum(1 for _, k, _ in trades if k == "BUY")
print(f"企稳信号建仓+信号低点止损: 终值={float(eq.iloc[-1]):.2f} 买入{nb}笔 MDD={mdd:.1%}")
print(f"对照: V4基线523.89/-29.7% | V4+阶段1过滤528.18/-19.4%")
for d, k, p in trades:
    print(f"   {d.date()} {k} @{p:.2f}")
