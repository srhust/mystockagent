# -*- coding: utf-8 -*-
"""验证 ETF 复权修复: 煤炭/房地产/钢铁/保险 修复前后对比。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import data  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]

for sector, sym in [("煤炭", "sh515220"), ("房地产", "sh512200"), ("钢铁", "sh515210"),
                    ("保险", "sh512070"), ("半导体", "sh512480")]:
    raw = data.get_etf_hist(sym, adjusted=False)
    adj = data.get_etf_hist(sym, adjusted=True)
    idx = dfs[sector]["close"]
    for tag, e in [("原始", raw), ("复权", adj)]:
        s = e.set_index("date")["close"]
        j = pd.DataFrame({"idx": idx, "e": s}).dropna()
        j = j[j.index >= "2021-07-01"]
        r = j.pct_change().dropna()
        yrs = (j.index[-1] - j.index[0]).days / 365.25
        ann = (j["e"].iloc[-1] / j["e"].iloc[0]) ** (1 / yrs) - 1
        corr = r["idx"].corr(r["e"])
        mx = r["e"].abs().max()
        print(f"  {sector:<4} {tag}: 年化 {ann * 100:+6.1f}%  相关 {corr:.3f}  单日极值 {mx * 100:+.1f}%")
    print()
