# -*- coding: utf-8 -*-
"""专户口径持股时长统计: 入场temp<12 → 出场(非金融85/金融75), 含笔间空仓天数。"""
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import strategy as strat  # noqa: E402

warnings.filterwarnings("ignore")
obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]
CATS = {n: m.get("cat", "") for n, m in meta.items()}

rows = []
for name, df in dfs.items():
    ex = 75 if CATS[name].startswith("金融") else 85
    pos = strat.position_target(df, mode="cold", exit_above=ex).values
    idx = df.index
    runs = []
    i = 0
    n = len(pos)
    while i < n:
        if pos[i] == 1:
            j = i
            while j + 1 < n and pos[j + 1] == 1:
                j += 1
            if j - i >= 5:
                runs.append((i, j))
            i = j + 1
        else:
            i += 1
    for k, (a, b) in enumerate(runs):
        gap = (idx[a] - idx[runs[k - 1][1]]).days if k > 0 else None
        rows.append({"sector": name, "cat": CATS[name], "days": b - a + 1, "gap": gap,
                     "entry": idx[a], "exit": idx[b]})

d = pd.DataFrame(rows)
print(f"全体(n={len(d)}): 持仓均值{d['days'].mean():.0f}天/中位{d['days'].median():.0f}天 | "
      f"笔间空仓均值{d['gap'].mean():.0f}天/中位{d['gap'].median():.0f}天")
for cat, g in d.groupby("cat"):
    if len(g) >= 8:
        print(f"  {cat}(n={len(g)}): 持仓均值{g['days'].mean():.0f}/中位{g['days'].median():.0f}天 | "
              f"空仓均值{g['gap'].mean():.0f}/中位{g['gap'].median():.0f}天")
d.to_csv(os.path.join(config.OUTPUT_DIR, "holding_days.csv"), index=False, encoding="utf-8-sig")
print("[done] output/holding_days.csv")
