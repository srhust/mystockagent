# -*- coding: utf-8 -*-
"""审查二十六轮: 再入场机制 — 缩小账户交易数与事后谷底数的差距。
R1基线: 事件120日去重(现状) | R2: 限时退出日回看20日, 若有A∪B信号→立即再入(相邻谷链式接续) | R3: 事件去重缩至60日。
冻结: orig 120日限时退出, T+1账本, MU单标的。归属: 每笔账本交易归入最近的事后谷(一对一), 报告覆盖谷数。"""
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
from p2_exit_rerun import run_position_ledger
import mu_af_v2 as M

c = M.c
f = build_features(c, [])
dd = f["dd252"]
ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
dp = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
v4 = (M.v4.reindex(c.index).fillna(0) > 0) & (M.v4.reindex(c.index).fillna(0).diff() > 0)
sig = pd.Series((v4.values | dp.values), index=c.index)
n = len(c)
pxs = c.values
HOLD = 120


def pos_variant(dedup, recheck):
    """dedup=事件去重天数; recheck=退出日回看天数(0=关闭)。"""
    sigv = sig.values
    evd, last = set(), -9999
    for i in range(n):
        if sigv[i] and i - last >= dedup:
            evd.add(i)
            last = i
    pos = np.zeros(n)
    in_pos = False
    entry_i = -999
    trades = 0
    i = 0
    while i < n:
        if not in_pos and i in evd:
            in_pos, entry_i = True, i
            trades += 1
        elif in_pos and i - entry_i >= HOLD:
            in_pos = False
            if recheck > 0 and (i - max([e for e in evd if e <= i], default=-9999)) <= recheck:
                in_pos, entry_i = True, i  # 立即再入(新持仓, T+1由账本执行)
                trades += 1
        pos[i] = 1.0 if in_pos else 0.0
        i += 1
    return pd.Series(pos, index=c.index), trades


# 九个事后合格谷
hi_i = lo_i = 0
hi = lo = pxs[0]
trend, pivots = 0, []
for i in range(1, n):
    x = pxs[i]
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
lows = sorted(set(i for k, i in pivots if k == "L"))
qual = [t for t in lows if n - (t + 1) >= 60 and float(pxs[t + 1:min(n, t + 121)].max() / pxs[t] - 1) >= 0.50]

print(f"=== 再入场机制对比(MU, orig限时退出冻结, 事件源=V4∪双路径) ===")
for label, dedup, recheck in [("R1基线(去重120)", 120, 0), ("R2退出回看20日再入", 120, 20), ("R3去重60日", 60, 0), ("R4=R2+R3", 60, 20)]:
    pos, tr_n = pos_variant(dedup, recheck)
    eq, led = run_position_ledger(c, pos)
    L = pd.DataFrame(led, columns=["d", "ret", "hold"])
    pf = L[L.ret > 0].ret.sum() / abs(L[L.ret <= 0].ret.sum()) if len(L) and (L.ret <= 0).any() else np.inf
    # 一对一归属: 每笔交易入口归入最近的事后谷(容差±240日)
    used, covered = set(), []
    for d2, r, h in led:
        ei = c.index.get_loc(d2)
        cands = [(abs(ei - t), t) for t in qual if t not in used and abs(ei - t) <= 240]
        if cands:
            _, t = min(cands)
            used.add(t)
            covered.append(t)
    yrs = [str(c.index[t].date().year) for t in covered]
    print(f"{label}: 账户交易{tr_n}笔 归属谷{len(used)}/9({','.join(yrs)}) 终值{float(eq.iloc[-1]):.1f} MDD{float((eq/eq.cummax()-1).min()):.0%} 胜率{(L.ret>0).mean():.0%} PF{pf:.2f} 均持仓{L.hold.mean():.0f}日")
