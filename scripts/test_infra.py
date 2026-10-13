# -*- coding: utf-8 -*-
"""基础设施自测: 参数透传 / ETF数据 / 风控覆盖层。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import data  # noqa: E402
from cycle_system.backtest import overlay_equity, run_backtest  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]

# 1. 参数透传
b_default = run_backtest(dfs, meta, mode="cold")
b_p10_70 = run_backtest(dfs, meta, mode="cold", strat_params={"enter_below": 10, "exit_above": 70})
print(f"[1] 参数透传: 默认12/75 CAGR {b_default['metrics']['cagr']:.3f} 笔数 {b_default['metrics']['n_trades']}")
print(f"    覆盖10/70 CAGR {b_p10_70['metrics']['cagr']:.3f} 笔数 {b_p10_70['metrics']['n_trades']}")
assert b_default["metrics"]["n_trades"] != b_p10_70["metrics"]["n_trades"] or \
    b_default["metrics"]["cagr"] != b_p10_70["metrics"]["cagr"], "参数覆盖未生效!"

# 2. ETF 数据
e = data.get_etf_hist("sh515220")
print(f"[2] ETF数据: 煤炭ETF {len(e)}行 {e['date'].min().date()}~{e['date'].max().date()} 列{list(e.columns)}")

# 3. 风控覆盖层
w = b_default["weights"]
closes = pd.DataFrame({n: d["close"] for n, d in dfs.items()}).reindex(w.index)
rets = closes.pct_change().fillna(0.0)
ov = overlay_equity(w, rets)
m0, m1 = b_default["metrics"], ov["metrics"]
print(f"[3] 基线: CAGR {m0['cagr']:.3f} MDD {m0['max_drawdown']:.3f} Sharpe {m0['sharpe']:.2f}")
print(f"    覆盖: CAGR {m1['cagr']:.3f} MDD {m1['max_drawdown']:.3f} Sharpe {m1['sharpe']:.2f}")
ov2 = overlay_equity(w, rets, vol_target=0.25)
print(f"    覆盖+voltarget: CAGR {ov2['metrics']['cagr']:.3f} MDD {ov2['metrics']['max_drawdown']:.3f}")
print("\n全部通过")
