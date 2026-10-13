# -*- coding: utf-8 -*-
"""专户三档比例测试: 40/30/30 vs 均分 vs 前重/后重。"""
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

from specialist import CATS, dfs, simulate_account  # noqa: E402  (导入即重算, 忽略其打印)

warnings.filterwarnings("ignore")

VARIANTS = [
    ("40/30/30", (0.4, 0.3, 0.3)),
    ("34/33/33", (1 / 3, 1 / 3, 1 / 3)),
    ("60/20/20", (0.6, 0.2, 0.2)),
    ("50/25/25", (0.5, 0.25, 0.25)),
    ("30/35/35", (0.3, 0.35, 0.35)),
]
print("variant|cat|n|mean|median|win|maxdd", flush=True)
for vn, uf in VARIANTS:
    rows = []
    for name, df in dfs.items():
        ex = 75 if CATS.get(name, "").startswith("金融") else 85
        for t in simulate_account(df, exit_=ex, unit_fracs=uf):
            t["cat"] = CATS.get(name, "")
            rows.append(t)
    d = pd.DataFrame(rows)
    for cat, g in d.groupby("cat"):
        if len(g) >= 8:
            print(f"{vn}|{cat}|{len(g)}|{g['ret'].mean() * 100:+.1f}|"
                  f"{g['ret'].median() * 100:+.1f}|{(g['ret'] > 0).mean() * 100:.0f}|"
                  f"{g['max_float_dd'].mean() * 100:.1f}", flush=True)
