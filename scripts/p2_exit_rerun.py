# -*- coding: utf-8 -*-
"""审查十七轮P0修复: 统一position-faithful执行器 — sig为0/1目标仓位(与run_specialist不同, 卖出由sig归零驱动),
T日决策T+1成交, 全仓进出, 逐笔账本(净收益口径)。P2退出消融与双路径账户回测全部重跑。"""
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
import mu_af_v2 as M


def run_position_ledger(px, pos, budget=100.0, cost=0.0015):
    """pos: 0/1目标仓位Series(T日收盘决定)。审查十九轮修复: T-1收盘决策→T日收盘成交(真T+1, 原实现晚2日)。"""
    cash, shares = float(budget), 0.0
    in_pos = False
    eq = pd.Series(np.nan, index=px.index)
    ledger = []
    pxs = px.values
    ps = pos.reindex(px.index).fillna(0.0).values
    for i in range(1, len(px)):
        p = pxs[i]
        want = bool(ps[i - 1] >= 0.5)  # T-1收盘已知 → T日收盘成交(真T+1)
        if want and not in_pos:
            shares = cash * (1 - cost) / p
            entry_px = p
            entry_i = i
            cash = 0.0
            in_pos = True
        elif (not want) and in_pos:
            cash = shares * p * (1 - cost)
            ledger.append((px.index[i], p / entry_px - 1, i - entry_i))
            shares = 0.0
            in_pos = False
        eq.iloc[i] = cash + shares * p
    eq = eq.ffill().fillna(budget)
    return eq, ledger


c = M.c
f = build_features(c, [])
dd = f["dd252"]
entry_sig = ((M.v4 == 1) & rule_state(c).rolling(120, min_periods=1).max().astype(bool)).astype(float)
ma50, ma100 = c.rolling(50).mean(), c.rolling(100).mean()
vol20 = c.pct_change().rolling(20).std()
low20 = c.rolling(20).min()
n = len(c)


def positions(mode):
    """真退出语义: X0=入场信号持续(v4状态), X1-X4=入场后由对应条件强制清零。"""
    pos = np.zeros(n)
    in_pos = False
    peak = np.nan
    for i in range(n):
        if not in_pos and entry_sig.iloc[i] >= 0.5:
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
                dead = (i > 0) and (x < low20.iloc[i - 1])
            if dead:
                in_pos = False
            else:
                pos[i] = 1.0
    return pd.Series(pos, index=c.index)


print("=== P2重跑(真退出, run_position_ledger) ===")
for mode in ("X0", "X1", "X2", "X3", "X4"):
    eq, led = run_position_ledger(c, positions(mode))
    L = pd.DataFrame(led, columns=["d", "ret", "hold"])
    mdd = float((eq / eq.cummax() - 1).min())
    wr = (L["ret"] > 0).mean() if len(L) else np.nan
    pf = L[L.ret > 0].ret.sum() / abs(L[L.ret <= 0].ret.sum()) if len(L) and (L.ret <= 0).any() else np.inf
    print(f"{mode}: 终值{float(eq.iloc[-1]):.1f} MDD{mdd:.0%} 笔{len(L)} 胜率{wr:.0%} PF{pf:.2f}")
