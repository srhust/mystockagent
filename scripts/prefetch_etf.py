# -*- coding: utf-8 -*-
"""预取全部行业 ETF 数据到本地缓存（供 ETF 回测离线快速迭代）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import config  # noqa: E402
from cycle_system import data  # noqa: E402

ok, fail = 0, []
for sector, (sym, name, _note) in config.ETF_MAP.items():
    df = data.get_etf_hist(sym)
    if df is not None and len(df) > 0:
        ok += 1
        print(f"  {sector:<6} {sym} {name:<10} {len(df):>5}行 {df['date'].min().date()}~{df['date'].max().date()}")
    else:
        fail.append((sector, sym))
        print(f"  {sector:<6} {sym} FAIL")
print(f"\nOK {ok}/{len(config.ETF_MAP)}, fail: {fail}")
