# -*- coding: utf-8 -*-
"""V2 引擎验收: run_backtest(mode='cold_v2') 必须复现研究模拟器 X4 数字。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import backtest as btengine  # noqa: E402
from cycle_system import strategy as strat  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]
n_fut = sum(1 for d in dfs.values() if "fut_mom20" in d.columns)
print(f"[check] 期货列覆盖: {n_fut}/{len(dfs)} 板块", flush=True)

for wstart, tag in [("2010-01-01", "2010-2026 全样本"), ("2017-01-01", "2017-2026 样本外")]:
    for mode in ("cold", "cold_v2"):
        r = btengine.run_backtest(dfs, meta, mode=mode, trailing_stop=False, start=wstart)
        m = r["metrics"]
        print(f"[engine] {tag} | {mode}: 年化{m['cagr'] * 100:+.1f}% MDD{m['max_drawdown'] * 100:.1f}% "
              f"Sharpe{m['sharpe']:.2f} 笔数{m['n_trades']}", flush=True)

# 当前信号快照(cold_v2 vs cold)
diff = []
for name, df in dfs.items():
    p1 = strat.position_target(df, mode="cold").iloc[-1]
    p2 = strat.position_target(df, mode="cold_v2").iloc[-1]
    if p1 != p2:
        diff.append(f"{name}: cold={p1} cold_v2={p2}")
print(f"[signal] 当前信号差异: {diff if diff else '无(当前无持仓受期货背离影响)'}", flush=True)
