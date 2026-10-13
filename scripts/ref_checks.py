# -*- coding: utf-8 -*-
"""独立参照计算: 3x3 参数子网格 + 相关性矩阵快照（用于交叉核验子代理报告）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system.backtest import run_backtest  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]

print("=== 参照1: 3x3 参数子网格 (全样本 cold, Sharpe / CAGR) ===")
for eb in (10, 12, 15):
    row = []
    for xa in (70, 75, 80):
        b = run_backtest(dfs, meta, mode="cold", strat_params={"enter_below": eb, "exit_above": xa})
        m = b["metrics"]
        row.append(f"eb{eb}/xa{xa}: S{m['sharpe']:.3f} C{m['cagr']:.3f} D{m['max_drawdown']:.2f} N{m['n_trades']}")
    print("  " + " | ".join(row))

print("\n=== 参照2: 板块相关性快照 (>0.65 对数, 250日) ===")
closes = pd.DataFrame({n: d["close"] for n, d in dfs.items()})
rets = closes.pct_change().iloc[-250:]
corr = rets.corr()
pairs = []
names = list(corr.columns)
for i in range(len(names)):
    for j in range(i + 1, len(names)):
        c = corr.iloc[i, j]
        if c > 0.65:
            pairs.append((names[i], names[j], round(float(c), 3)))
pairs.sort(key=lambda x: -x[2])
print(f"  高相关对数量: {len(pairs)} / {len(names)*(len(names)-1)//2}")
for a, b_, c in pairs[:15]:
    print(f"    {a} ~ {b_}: {c}")
