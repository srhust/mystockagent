# -*- coding: utf-8 -*-
"""朴素资金缩放 vs 精巧风控 overlay 的对照。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system.backtest import run_backtest  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
b = run_backtest(obj["dfs"], obj["meta"], mode="cold")
eq = b["equity"]["equity"]
m = b["metrics"]
yrs = (eq.index[-1] - eq.index[0]).days / 365.25
for frac in (0.5, 0.6):
    e2 = 1 + frac * (eq - 1)
    tot = e2.iloc[-1] - 1
    cagr = (1 + tot) ** (1 / yrs) - 1
    mdd = (e2 / e2.cummax() - 1).min()
    vol = e2.pct_change().std() * (250 ** 0.5)
    print(f"{int(frac * 100)}% 资金: CAGR {cagr * 100:+.1f}% MDD {mdd * 100:.1f}% "
          f"vol {vol * 100:.1f}% Calmar {cagr / abs(mdd):.2f}")
print(f"100%(基线): CAGR {m['cagr'] * 100:+.1f}% MDD {m['max_drawdown'] * 100:.1f}%")
