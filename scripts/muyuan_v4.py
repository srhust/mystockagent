# -*- coding: utf-8 -*-
"""牧原V4: 干净baostock数据, dd-50入场/-25修复出场。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

fp = os.path.join(config.DATA_DIR, "stock_002714.csv")
df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
c = df.set_index("date")["close"].astype(float)
c = c[c.index >= "2015-01-01"]
dd = (c / c.cummax() - 1.0).values
cv, idx, n = c.values, c.index, len(c)
i, trs = 0, []
inp = False
while i < n:
    if not inp and np.isfinite(dd[i]) and dd[i] <= -0.50:
        inp, ep, ei = True, cv[i], i
    elif inp:
        if dd[i] >= -0.25 or (i - ei) > 1800:
            trs.append((idx[ei].date(), idx[i].date(), cv[i] / ep - 1, i - ei))
            inp = False
    i += 1
r = [x[2] for x in trs]
print(f"V4牧原(dd-50/-25): {len(trs)}笔 均值{np.mean(r) * 100:+.1f}% 胜{sum(x > 0 for x in r)}/{len(r)}")
for a, b, v, d in trs:
    print(f"  {a} -> {b}  {v * 100:+.1f}%  ({d}天)")
