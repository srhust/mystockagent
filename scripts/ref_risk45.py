# -*- coding: utf-8 -*-
"""独立核验: 方案4 相关性聚类限额(corr>0.65) 与方案5(+回撤控制)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system.backtest import overlay_equity, run_backtest  # noqa: E402
from risk_compare import cluster_by_correlation  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]

b0 = run_backtest(dfs, meta, mode="cold")
closes = pd.DataFrame({n: d["close"] for n, d in dfs.items()}).reindex(b0["weights"].index)
rets = closes.pct_change().fillna(0.0)
corr, clusters, label = cluster_by_correlation(closes.pct_change(), 0.65)
sizes = sorted((len(g) for g in clusters), reverse=True)
print(f"簇数 {len(clusters)}, 簇大小分布 {sizes}")
meta2 = {n: dict(m, cat=f"簇{label[n]}") for n, m in meta.items()}
b4 = run_backtest(dfs, meta2, mode="cold", per_category_max=2)
m4 = b4["metrics"]
print(f"方案4: CAGR {m4['cagr']:+.3f} 波动 {m4['ann_vol']:.3f} Sharpe {m4['sharpe']:.2f} "
      f"MDD {m4['max_drawdown']:.3f} 笔数 {m4['n_trades']}")
rets4 = closes.reindex(b4["weights"].index).pct_change().fillna(0.0)
o5 = overlay_equity(b4["weights"], rets4)
m5 = o5["metrics"]
print(f"方案5: CAGR {m5['cagr']:+.3f} 波动 {m5['ann_vol']:.3f} Sharpe {m5['sharpe']:.2f} MDD {m5['max_drawdown']:.3f}")
