# -*- coding: utf-8 -*-
"""消融B: START信号的分量归因。比较 完整START vs 无广度 vs 无RS vs 仅价格突破 的
事件数与有效启动Precision(事件级, 标签=先涨15%×s先于先跌10%×s)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from reversal_detector import effective_start

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]
ref = pd.read_csv(os.path.join(config.DATA_DIR, "idx_hs300.csv"), parse_dates=["date"]).set_index("date")["close"].astype(float)

VARIANTS = ["full", "no_bd", "no_rs", "price_only"]
stats = {v: {"n": 0, "lab": []} for v in VARIANTS}


def events(mask, gap=60):
    idx = [i for i in range(len(mask)) if mask[i]]
    return [s for k, s in enumerate(idx) if k == 0 or s - idx[k - 1] > gap]


for name, d in dfs.items():
    c = d["close"].dropna()
    c.index = pd.to_datetime(c.index)
    c = c[c.index >= "2010-01-01"]
    if len(c) < 1250:
        continue
    br = d["breadth"].reindex(c.index) if "breadth" in d else None
    dd252 = c / c.rolling(252, min_periods=120).max() - 1.0
    ma20 = c.rolling(20).mean()
    rs = c / ref.reindex(c.index)
    rs20 = rs.pct_change(20)
    rs_long = rs / rs.rolling(252, min_periods=120).mean() - 1.0
    bd = (br - br.shift(20)) if br is not None else pd.Series(0.0, index=c.index)
    watch = (dd252 <= -0.25) | ((dd252 > -0.25) & (dd252 <= -0.08) & (rs_long > -0.15))
    brk20 = c > c.rolling(20).max().shift(1)
    lab = effective_start(c).values
    conds = {
        "full": watch & brk20 & (c > ma20) & (rs20 > 0) & (bd > 0.01),
        "no_bd": watch & brk20 & (c > ma20) & (rs20 > 0),
        "no_rs": watch & brk20 & (c > ma20) & (bd > 0.01),
        "price_only": watch & brk20,
    }
    for v, m in conds.items():
        for s in events(m.values):
            stats[v]["n"] += 1
            if np.isfinite(lab[s]):
                stats[v]["lab"].append(int(lab[s]))

print(f"{'变体':<12}{'事件数':>8}{'Precision':>12}")
for v in VARIANTS:
    L = stats[v]["lab"]
    p = f"{np.mean(L)*100:.0f}%" if L else "-"
    print(f"{v:<12}{stats[v]['n']:>8}{p:>12}")
print("\n解读: full较no_bd/no_rs的Precision增量=该分量贡献; price_only=纯突破基线。")
