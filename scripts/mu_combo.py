# -*- coding: utf-8 -*-
"""审查七轮组合实验: G = A型深寒入场 + 首档逆势加仓(-15%) + 剩余档突破顺势加仓(状态机门控)。
对照 A(纯逆势) / E(纯顺势)。同引擎hybrid模式, 8/8测试通过。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

from scripts.mu_af_v2 import v4, brk, ext85, c, cal, cycles  # 已验证信号(副作用: 重印A-F表)
from engine import run_specialist, metrics

BUDGET = 100.0


def state_signals(esig, asig, ext):
    pos, adds = 0, 0
    entry_s, add_s = np.zeros(len(cal)), np.zeros(len(cal))
    ev = ext.reindex(cal).fillna(0).values
    es, as_ = esig.reindex(cal).fillna(0).values, asig.reindex(cal).fillna(0).values
    for i in range(len(cal)):
        if pos and ev[i] >= 1:
            pos, adds = 0, 0
        elif pos == 0 and es[i] >= 1:
            pos, adds, entry_s[i] = 1, 0, 1.0
        elif pos and adds < 2 and as_[i] >= 1:
            add_s[i], adds = 1.0, adds + 1
    return pd.Series(entry_s, index=cal), pd.Series(add_s, index=cal)


entry_g, add_g = state_signals(v4, brk, ext85)
CONFIGS = [
    ("A 逆势分批(基准)", v4, None, (0.30, 0.35, 0.35), "signal"),
    ("E 纯顺势加仓", v4, brk, (0.30,), "signal"),
    ("G 组合: 首档逆势+次档顺势", entry_g, brk, (0.30, 0.35, 0.35), "hybrid"),
]
print(f"{'配置':<30}{'CAGR':>8}{'Sharpe':>8}{'MDD':>8}{'平均暴露':>8}{'BUY':>5}{'ADD':>5}")
for tag, esig, asig, tr, mode in CONFIGS:
    entry_sig = esig if asig is None else entry_g
    add_sig = asig
    eq, fills, _ = run_specialist(c, entry_sig.reindex(cal).fillna(0), ext85.reindex(cal).fillna(0), cal,
                                  budget=BUDGET, cost=0.001, tranches=tr, add_sig=add_sig,
                                  add_frac=0.35, max_adds=2, add_mode=mode)
    m = metrics(eq.dropna())
    n_add = sum(1 for f in fills if f[1] == "ADD")
    n_buy = sum(1 for f in fills if f[1] == "BUY")
    exp = eq.attrs.get("avg_exposure", 0)
    print(f"{tag:<30}{m['CAGR']*100:>7.2f}%{m['Sharpe']:>8.2f}{m['MDD']*100:>7.1f}%{exp*100:>7.1f}%{n_buy:>5}{n_add:>5}")
