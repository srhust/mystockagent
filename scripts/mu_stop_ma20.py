# -*- coding: utf-8 -*-
"""审查十四轮: 止损逻辑重研 — 企稳判断的证伪条件=收盘跌回MA20之下(小代价离场), 非固定20日低点(大回撤)。
入场=阶段2信号(MA20收复+上行+深跌区250日内, 去重20日); 离场=收盘<MA20(T+1执行); 下次企稳再进(波段自然捕捉, 深跌窗过期后自动停)。"""
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
breach = (c < ma20).values
n = len(c)
cost = 0.0015
cash, shares = 100.0, 0.0
in_pos = False
pend = None
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
            trades.append((c.index[d], "BUY", pdx))
            cash = 0.0
            in_pos = True
        else:
            cash = shares * pdx * (1 - cost)
            trades.append((c.index[d], "SELL", pdx))
            shares = 0.0
            in_pos = False
        pend = None
    if s2[i] and i - last_sig >= 20 and not in_pos and pend is None:
        last_sig = i
        pend = ("buy", i + 1)
    elif in_pos and breach[i] and pend is None:
        pend = ("sell", i + 1)
    equity.iloc[i] = cash + shares * px
eq = equity.dropna()
mdd = float((eq / eq.cummax() - 1).min())
nb = sum(1 for _, k, _ in trades if k == "BUY")
wins, losses = [], []
entry_px = None
for d, k, p in trades:
    if k == "BUY":
        entry_px = p
    elif entry_px:
        (wins if p > entry_px else losses).append(p / entry_px - 1)
print(f"MA20证伪止损版: 终值={float(eq.iloc[-1]):.2f} 往返{nb}笔 MDD={mdd:.1%} "
      f"赢{len(wins)}笔(均{np.mean(wins):+.1%}) 亏{len(losses)}笔(均{np.mean(losses):+.1%})" if losses else f"MA20证伪止损版: 终值={float(eq.iloc[-1]):.2f} 往返{nb}笔 MDD={mdd:.1%} 赢{len(wins)}笔 亏0笔")
print(f"对照: V4基线523.89/-29.7% | V4+阶段1过滤528.18/-19.4% | 20日低点止损版11036/-82.3%")
