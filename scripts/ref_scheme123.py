# -*- coding: utf-8 -*-
"""独立验证方案1/2/3（干净数据, 正确口径: weights + 板块级 rets）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system.backtest import run_backtest, overlay_equity  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]
b0 = run_backtest(dfs, meta, mode="cold")
cal = b0["weights"].index
closes = pd.DataFrame({n: d["close"] for n, d in dfs.items()}).reindex(cal)
rets = closes.pct_change().fillna(0.0)
m = b0["metrics"]
print(f"基线:    CAGR {m['cagr'] * 100:+.1f}% MDD {m['max_drawdown'] * 100:.1f}% Sharpe {m['sharpe']:.2f}")

DD = dict(dd_soft=-0.15, dd_hard=-0.30, recover_above=-0.08, soft_scale=0.5)
for name, kw in [
    ("方案1 回撤控制", dict(vol_target=None, **DD)),
    ("方案2 vol目标 ", dict(vol_target=0.25, dd_soft=-100.0, dd_hard=-100.0, recover_above=-0.08, soft_scale=0.5)),
    ("方案3 回撤+vol", dict(vol_target=0.25, **DD)),
]:
    o = overlay_equity(b0["weights"], rets, cost_rate=0.0015, **kw)
    mm = o["metrics"]
    print(f"{name}: CAGR {mm['cagr'] * 100:+.1f}% MDD {mm['max_drawdown'] * 100:.1f}% Sharpe {mm['sharpe']:.2f}")

# 诊断: 回撤状态机穿越统计（正确符号）
eq = b0["equity"]["equity"]
dd = eq / eq.cummax() - 1.0
below_hard = (dd <= -0.30).astype(int)
crossings = int((below_hard.diff() == 1).sum())
w_total = b0["weights"].sum(axis=1)
print(f"\n诊断: 基线权益穿越-30% {crossings} 次, 低于-30%共 {int(below_hard.sum())} 天, "
      f"低于-15%共 {int((dd <= -0.15).sum())} 天, 平均仓位 {w_total.mean():.2f}")
