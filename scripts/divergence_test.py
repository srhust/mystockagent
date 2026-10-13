# -*- coding: utf-8 -*-
"""深寒×MACD底背离 分离度验证: 底背离是否提升深寒入场质量。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

sys.path.insert(0, r"E:\mystockagent\cycle_system")
from signal_engine import macd_divergence  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]
div_n = nod_n = 0
div_r, nod_r = [], []
for name, d in dfs.items():
    c = d["close"].dropna()
    c.index = pd.to_datetime(c.index)
    c = c[c.index >= "2010-01-01"]
    if len(c) < 1250:
        continue
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    temp = 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)
    f250 = c.shift(-250) / c - 1.0
    div = macd_divergence(c)
    sig = (temp < 12) & (temp.shift(1) >= 12)  # 深寒事件起点
    for i in np.where(sig)[0]:
        if i + 250 >= len(c) or pd.isna(f250.iloc[i]):
            continue
        d_win = div["bull_div"].iloc[max(0, i - 30):i + 1]
        if d_win.any():
            div_n += 1
            div_r.append(float(f250.iloc[i]))
        else:
            nod_n += 1
            nod_r.append(float(f250.iloc[i]))
a, b = np.array(div_r), np.array(nod_r)
print(f"深寒事件起点: 有底背离 {div_n} 笔 250日前向 均值{a.mean() * 100:+.1f}% 中位{np.median(a) * 100:+.1f}% 胜率{(a > 0).mean() * 100:.0f}%")
print(f"              无底背离 {nod_n} 笔 250日前向 均值{b.mean() * 100:+.1f}% 中位{np.median(b) * 100:+.1f}% 胜率{(b > 0).mean() * 100:.0f}%")
