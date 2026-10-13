# -*- coding: utf-8 -*-
"""审查八轮待办两项:
① E vs A 同暴露对照: 扫E的add_frac对齐A的平均暴露10.6%。
② 漏检周期补召: fastV信号(40日内跌≥25% + 首个放量长阳)作为V4补充入场, 检验MU-06/09是否召得回、代价几何。
MU-01/02为数据预热期(1250d窗口未满), 不可修复, 如实归为数据边界。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

from scripts.mu_af_v2 import v4, brk, ext85, c, cal, cycles
from engine import run_specialist, metrics

BUDGET = 100.0

# ① 同暴露: E配置扫add_frac
print("=== ① E vs A 同暴露对照 ===")
a_exp = None
rows = {}
for tag, tr, asig, frac, mode in [("A 逆势分批", (0.30, 0.35, 0.35), None, 0.35, "signal"),
                                  ("E add_frac=0.35", (0.30,), brk, 0.35, "signal"),
                                  ("E add_frac=0.25", (0.30,), brk, 0.25, "signal"),
                                  ("E add_frac=0.20", (0.30,), brk, 0.20, "signal")]:
    def state(esig, asig):
        pos, adds = 0, 0
        en, ad = np.zeros(len(cal)), np.zeros(len(cal))
        ev = ext85.reindex(cal).fillna(0).values
        es, as_ = esig.reindex(cal).fillna(0).values, asig.reindex(cal).fillna(0).values if asig is not None else None
        for i in range(len(cal)):
            if pos and ev[i] >= 1:
                pos, adds = 0, 0
            elif pos == 0 and es[i] >= 1:
                pos, adds, en[i] = 1, 0, 1.0
            elif pos and adds < 2 and as_ is not None and as_[i] >= 1:
                ad[i], adds = 1.0, adds + 1
        return pd.Series(en, index=cal), (pd.Series(ad, index=cal) if asig is not None else None)
    en, ad = state(v4, asig)
    eq, fills, _ = run_specialist(c, en.reindex(cal).fillna(0), ext85.reindex(cal).fillna(0), cal,
                                  budget=BUDGET, cost=0.001, tranches=tr, add_sig=ad,
                                  add_frac=frac, max_adds=2, add_mode=mode)
    m = metrics(eq.dropna())
    exp = eq.attrs.get("avg_exposure", 0)
    if tag.startswith("A"):
        a_exp = exp
    rows[tag] = (m, exp)
    print(f"{tag:<18} CAGR {m['CAGR']*100:+.2f}% Sharpe {m['Sharpe']:.2f} MDD {m['MDD']*100:.1f}% 暴露{exp*100:.1f}%")
print(f"(A暴露={a_exp*100:.1f}%, 最接近的E档位为同暴露口径)")

# ② 漏检补召: fastV
print("\n=== ② fastV漏检补召实验 ===")
ret40 = c / c.shift(40) - 1
vol = pd.Series(1.0, index=cal)  # MU.csv无量的可靠复权口径, 用价格强度替代放量: 长阳=当日涨幅>3%
strong_up = (c.pct_change() > 0.03).astype(float)
fastv = ((ret40 < -0.25) & (strong_up > 0) & (c > c.shift(1).rolling(5).max() * 0.995)).astype(float)
fastv = fastv.reindex(cal).fillna(0)
v4f = ((v4 > 0) | (fastv > 0)).astype(float)
for tag, sig0 in [("V4(对照)", v4), ("V4+fastV", v4f)]:
    eq, fills, _ = run_specialist(c, sig0.reindex(cal).fillna(0), ext85.reindex(cal).fillna(0), cal,
                                  budget=BUDGET, cost=0.001, tranches=(0.30, 0.35, 0.35))
    m = metrics(eq.dropna())
    n_buy = sum(1 for f in fills if f[1] == "BUY")
    print(f"{tag:<10} CAGR {m['CAGR']*100:+.2f}% Sharpe {m['Sharpe']:.2f} MDD {m['MDD']*100:.1f}% BUY {n_buy}")
