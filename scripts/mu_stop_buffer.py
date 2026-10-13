# -*- coding: utf-8 -*-
"""审查十四轮: ①MA20证伪止损+3%缓冲带 ②原20日低点止损版的1/3仓位化(每信号1/3仓, 止损清该笔)。对照110倍全仓版。"""
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


def run_buf(buf):
    cash, shares, in_pos, pend, last_sig = 100.0, 0.0, False, None, -99
    equity = pd.Series(np.nan, index=c.index)
    nb = 0
    for i in range(1, n):
        px = c.iloc[i]
        if pend is not None:
            kind, d = pend
            pdx = c.iloc[d]
            if kind == "buy":
                shares = cash * (1 - cost) / pdx
                cash = 0.0
                in_pos = True
                nb += 1
            else:
                cash = shares * pdx * (1 - cost)
                shares = 0.0
                in_pos = False
            pend = None
        if s2[i] and i - last_sig >= 20 and not in_pos and pend is None:
            last_sig = i
            pend = ("buy", i + 1)
        elif in_pos and c.iloc[i] < ma20.iloc[i] * (1 - buf) and pend is None:
            pend = ("sell", i + 1)
        equity.iloc[i] = cash + shares * px
    eq = equity.dropna()
    return float(eq.iloc[-1]), float((eq / eq.cummax() - 1).min()), nb


print("=== ① 缓冲带扫描(MA20证伪+缓冲) ===")
for buf in (0.0, 0.03, 0.05, 0.08):
    v, mdd, nb = run_buf(buf)
    print(f"buffer={buf:.0%}: 终值={v:.2f} MDD={mdd:.1%} 买入{nb}笔")


def run_scaled(frac, stop_mode):
    cash, shares, in_pos, pend, last_sig = 100.0, 0.0, False, None, -99
    equity = pd.Series(np.nan, index=c.index)
    stop_px = np.nan
    nb = 0
    for i in range(1, n):
        px = c.iloc[i]
        if pend is not None:
            kind, d = pend
            pdx = c.iloc[d]
            if kind == "buy":
                spend = cash * frac
                shares += spend * (1 - cost) / pdx
                cash -= spend
                stop_px = float(c.iloc[max(0, d - 19):d + 1].min())
                in_pos = True
                nb += 1
            else:
                cash += shares * pdx * (1 - cost)
                shares = 0.0
                in_pos = False
            pend = None
        if s2[i] and i - last_sig >= 20 and not in_pos and pend is None:
            last_sig = i
            pend = ("buy", i + 1)
        elif in_pos and stop_mode == "low20" and c.iloc[i] < stop_px and pend is None:
            pend = ("sell", i + 1)
        equity.iloc[i] = cash + shares * px
    eq = equity.dropna()
    return float(eq.iloc[-1]), float((eq / eq.cummax() - 1).min()), nb


print("=== ② 20日低点止损版仓位化 ===")
for frac in (1 / 3, 0.5):
    v, mdd, nb = run_scaled(frac, "low20")
    print(f"每信号{frac:.0%}仓+20日低点止损: 终值={v:.2f} MDD={mdd:.1%} 买入{nb}笔")
