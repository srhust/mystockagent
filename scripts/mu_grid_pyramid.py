# -*- coding: utf-8 -*-
"""审查十四轮: 仓位管理逼近低点成本 — 深跌区分档接刀(dd252跌破-35/-45/-55/-65%各买25%预算) + 阶段2确认后离场。
度量: 终值/MDD/暴露 + 每轮"平均买入成本 vs 事后谷价"。纪律: T日收盘确认T+1收盘成交, 费0.15%。"""
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
s2 = ((c > ma20) & (ma20 > ma20.shift(5))).values
state = (dd <= -0.35).values
n = len(c)
GRIDS = [(-0.35, 0.25), (-0.45, 0.25), (-0.55, 0.25), (-0.65, 0.25)]
cost = 0.0015
BUD0 = 100.0
cash, shares = BUD0, 0.0
pend_buy, pend_sell = [], []  # (执行日i, 金额/全部)
grid_hit = [False] * 4
state_run = 0
buys, sells = [], []
equity = pd.Series(np.nan, index=c.index)
for i in range(1, n):
    px = c.iloc[i]
    # T+1执行
    for d, amt in pend_buy[:]:
        sh = amt * (1 - cost) / c.iloc[d]
        if amt <= cash + 1e-9:
            shares += sh
            cash -= amt
            buys.append((d, float(c.iloc[d]), amt, sh))
        pend_buy.remove((d, amt))
    for d in pend_sell[:]:
        if shares > 0:
            cash += shares * px * (1 - cost)
            sells.append((d, float(px), shares))
            shares = 0.0
        pend_sell.remove(d)
    # T日决策(用T日收盘信息)
    state_run = state_run + 1 if state[i] else 0
    if state_run == 5:
        grid_hit = [False] * 4  # 新深跌区开启
    if state[i]:
        for gi, (g, sz) in enumerate(GRIDS):
            if not grid_hit[gi] and dd.iloc[i] <= g and cash >= BUD0 * sz * 0.5:
                grid_hit[gi] = True
                pend_buy.append((i + 1, min(BUD0 * sz, cash)))  # T+1执行
    if s2[i] and shares > 0:
        pend_sell.append((i + 1,))  # 阶段2确认, T+1清仓
        grid_hit = [False] * 4
    equity.iloc[i] = cash + shares * px
eq = equity.dropna()
mdd = float((eq / eq.cummax() - 1).min())
print(f"分档接刀+阶段2离场: 终值={float(eq.iloc[-1]):.2f} 买入{len(buys)}笔 卖出{len(sells)}笔 MDD={mdd:.1%} "
      f"总投入{BUD0 - cash + sum(a for _, _, a, _ in buys) - BUD0 + cash:.1f}(校准) 持仓日={int((shares > 0).sum()) if False else int((eq.index.to_series().diff().dt.days.notna()).sum())}")
print(f"对照: V4基线523.89/-29.7% | V4+阶段1过滤528.18/-19.4%")
# 成本 vs 事后谷底: 合格谷
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
print("\n=== 每轮周期: 平均买入成本 vs 事后谷价 ===")
for t in qual:
    pre = [(d, p, a, sh) for d, p, a, sh in buys if t - 200 <= d <= t + 10]
    if not pre:
        print(f"{c.index[t].date()}: 无买入(状态未触发)")
        continue
    tot_a = sum(a for _, _, a, _ in pre)
    tot_s = sum(sh for _, _, _, sh in pre)
    avg = tot_a / tot_s if tot_s else np.nan
    tp = float(c.iloc[t])
    print(f"{c.index[t].date()}: 买入{len(pre)}笔 投入{tot_a:.1f} 均价{avg:.2f} 谷价{tp:.2f} 成本偏离{(avg/tp-1):+.1%}")
