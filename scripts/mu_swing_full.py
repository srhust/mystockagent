# -*- coding: utf-8 -*-
"""审查十四轮: 波段完整捕捉版 — 证伪止损(MA20×0.95)出场; 回场分两种: ①周期底入场=阶段2信号(深跌250日内)
②上涨波段回场=收盘重新上穿MA20 且 dd252>-35%(非新深跌) — 每次回调洗出、每次收复接回, 主升段不漏。"""
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
above = (c > ma20).values
re_entry = above & (dd > -0.35).values  # 上穿条件在循环内判前一日
n = len(c)
cost = 0.0015
BUF = 0.05
cash, shares, in_pos, pend, last_sig, last_re = 100.0, 0.0, False, None, -99, -99
equity = pd.Series(np.nan, index=c.index)
trades = []
for i in range(1, n):
    px = c.iloc[i]
    if pend is not None:
        kind, d = pend
        pdx = c.iloc[d]
        if kind == "buy":
            shares = cash * (1 - cost) / pdx
            trades.append((c.index[d], "BUY", pdx))
            cash = 0.0
            in_pos = True
        else:
            cash = shares * pdx * (1 - cost)
            trades.append((c.index[d], "SELL", pdx))
            shares = 0.0
            in_pos = False
        pend = None
    want_buy = False
    if s2[i] and i - last_sig >= 20 and not in_pos and pend is None:
        last_sig = i
        want_buy = True
    elif (not in_pos and pend is None and i > 0 and above[i] and not above[i - 1]
          and dd.iloc[i] > -0.35 and i - last_re >= 5):
        last_re = i
        want_buy = True
    if want_buy:
        pend = ("buy", i + 1)
    elif in_pos and pend is None and c.iloc[i] < ma20.iloc[i] * (1 - BUF):
        pend = ("sell", i + 1)
    equity.iloc[i] = cash + shares * px
eq = equity.dropna()
mdd = float((eq / eq.cummax() - 1).min())
nb = sum(1 for _, k, _ in trades if k == "BUY")
wins = losses = 0
entry_px = None
for d, k, p in trades:
    if k == "BUY":
        entry_px = p
    elif entry_px:
        if p > entry_px:
            wins += 1
        else:
            losses += 1
        entry_px = None
print(f"波段完整捕捉版(buffer5%): 终值={float(eq.iloc[-1]):.2f} MDD={mdd:.1%} 买入{nb}笔 赢{wins}/亏{losses}")
print(f"对照: V4基线523.89/-29.7% | buffer8%无波段4249/-64.8% | 永不离场孤例11037/-82.3%")
