# -*- coding: utf-8 -*-
"""诊断分红表口径: 打印全部行 + 复权序列的单日跳变日。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

from cycle_system import data  # noqa: E402

for sym, name in [("sh515220", "煤炭"), ("sh512200", "房地产"), ("sh515210", "钢铁")]:
    div = data.get_etf_dividends(sym)
    print(f"=== {sym} {name} 分红表全部行 ===")
    print(div.to_string())
    adj = data.get_etf_hist(sym, adjusted=True)
    r = adj["close"].pct_change()
    big = adj.loc[r.abs() > 0.05, ["date", "close"]].copy()
    big["ret"] = r[r.abs() > 0.05] * 100
    print(f"复权后 |单日收益|>5% 的日子:")
    print(big.to_string(index=False) if len(big) else "  无")
    print()
