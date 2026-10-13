# -*- coding: utf-8 -*-
"""独立参照: walk-forward 第1折手工复算（训练≤2016网格选参 → 测试2017-2018）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system.backtest import run_backtest  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]

# 训练期网格（3x3 子网格足够交叉核验）
best = None
print("=== 训练期 (≤2016-12-31) 3x3 网格 ===")
for eb in (10, 12, 15):
    for xa in (70, 75, 80):
        b = run_backtest(dfs, meta, mode="cold", end="2016-12-31",
                         strat_params={"enter_below": eb, "exit_above": xa})
        m = b["metrics"]
        print(f"  eb{eb}/xa{xa}: Sharpe {m['sharpe']:.3f} CAGR {m['cagr']:.3f} N {m['n_trades']}")
        if best is None or m["sharpe"] > best[0]:
            best = (m["sharpe"], eb, xa)
print(f"\n训练期最优: enter={best[1]} exit={best[2]} (训练Sharpe {best[0]:.3f})")

# 测试期
for label, eb, xa in [("选中参数", best[1], best[2]), ("默认12/75", 12, 75)]:
    b = run_backtest(dfs, meta, mode="cold", start="2017-01-01", end="2018-12-31",
                     strat_params={"enter_below": eb, "exit_above": xa})
    m = b["metrics"]
    print(f"测试期2017-18 {label} (eb{eb}/xa{xa}): CAGR {m['cagr']:.3f} Sharpe {m['sharpe']:.3f} "
          f"MDD {m['max_drawdown']:.3f} N {m['n_trades']}")

# 基准: 同窗口等权 + 沪深300
from cycle_system import data  # noqa: E402
from cycle_system.backtest import benchmark_equal_weight, benchmark_index  # noqa: E402
bw = benchmark_equal_weight(dfs, start="2017-01-01", end="2018-12-31")
r = bw["equity"].pct_change().dropna()
yrs = max((bw.index[-1] - bw.index[0]).days / 365.25, 1e-9)
print(f"等权2017-18: CAGR {(bw['equity'].iloc[-1]) ** (1 / yrs) - 1:.3f}")
bench = data.get_benchmark()
bi = benchmark_index(bench.set_index("date")["close"], dfs, start="2017-01-01", end="2018-12-31")
yrs2 = max((bi.index[-1] - bi.index[0]).days / 365.25, 1e-9)
print(f"沪深300 2017-18: CAGR {(bi['equity'].iloc[-1]) ** (1 / yrs2) - 1:.3f}")
