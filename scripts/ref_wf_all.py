# -*- coding: utf-8 -*-
"""独立参照: 默认12/75在5个walk-forward测试窗口的分段表现 + 参数网格补齐。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system.backtest import run_backtest  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]

print("=== 默认 12/75 各测试窗口分段 ===")
seg = 1.0
WINDOWS = [("2017-01-01", "2018-12-31"), ("2019-01-01", "2020-12-31"),
           ("2021-01-01", "2022-12-31"), ("2023-01-01", "2024-12-31"),
           ("2025-01-01", "2026-12-31")]
for s, e in WINDOWS:
    b = run_backtest(dfs, meta, mode="cold", start=s, end=e)
    m = b["metrics"]
    eq_end = b["equity"]["equity"].iloc[-1] / b["equity"]["equity"].iloc[0]
    seg *= eq_end
    print(f"  {s[:4]}~{e[:4]}: CAGR {m['cagr']:+.3f} Sharpe {m['sharpe']:+.2f} "
          f"MDD {m['max_drawdown']:.3f} N{m['n_trades']} 段末净值{eq_end:.3f}")
yrs = 9.8  # 2017-01 ~ 2026-10 约9.8年
print(f"  链接乘积(2017-01~2026-10): 总收益 {seg - 1:+.3f}, 年化 {seg ** (1 / yrs) - 1:+.3f}")

print("\n=== 参数网格补齐 (Sharpe) ===")
for eb in (8,):
    row = []
    for xa in (65, 70, 75, 80, 85):
        m = run_backtest(dfs, meta, mode="cold",
                         strat_params={"enter_below": eb, "exit_above": xa})["metrics"]
        row.append(f"xa{xa}:{m['sharpe']:.2f}/C{m['cagr']:.3f}")
    print(f"  eb{eb}: " + " ".join(row))
for eb in (10, 12):
    row = []
    for xa in (65, 85):
        m = run_backtest(dfs, meta, mode="cold",
                         strat_params={"enter_below": eb, "exit_above": xa})["metrics"]
        row.append(f"xa{xa}:{m['sharpe']:.2f}/C{m['cagr']:.3f}")
    print(f"  eb{eb}: " + " ".join(row))
