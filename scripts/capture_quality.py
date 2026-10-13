# -*- coding: utf-8 -*-
"""捕获质量指标 v2(口径统一, 全事件级):
事件 = V4入场信号的连续段(>60日间隔折叠为起点)。
周期(冻结) = dd谷底<=-30% 且两谷底间存在dd>=-15%修复段。
Recall = 命中周期/总周期; Precision = 落在周期谷底±60日的事件/总事件。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]
TC, TE, MAE = [], [], []
cyc_all = ev_all = hit_c = hit_e = 0
for name, d in dfs.items():
    c = d["close"].dropna()
    c.index = pd.to_datetime(c.index)
    c = c[c.index >= "2010-01-01"]
    if len(c) < 1250:
        continue
    dd = (c / c.cummax() - 1.0).values
    n = len(c)
    mins = [i for i in range(1, n - 1)
            if np.isfinite(dd[i]) and dd[i] <= -0.30 and dd[i] <= dd[i - 1] and dd[i] <= dd[i + 1]]
    cyc = []
    for i in mins:
        if cyc:
            seg = dd[cyc[-1]:i]
            if not (np.isfinite(seg) & (seg >= -0.15)).any():
                if dd[i] < dd[cyc[-1]]:
                    cyc[-1] = i
                continue
        cyc.append(i)
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    temp = 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60).values
    dep = pd.Series(-dd).rolling(1250, min_periods=250).max().values
    T = np.maximum(0.30, 0.70 * dep)
    sig = [i for i in range(n) if np.isfinite(dd[i]) and ((np.isfinite(temp[i]) and temp[i] < 12) or dd[i] <= -T[i])]
    ev = [s for k, s in enumerate(sig) if k == 0 or s - sig[k - 1] > 60]
    cyc_all += len(cyc)
    ev_all += len(ev)
    for t0 in cyc:
        if any(abs(s - t0) <= 60 for s in ev):
            hit_c += 1
    for s in ev:
        near = [t0 for t0 in cyc if abs(s - t0) <= 60]
        if near:
            hit_e += 1
            TC.append(s - min(near, key=lambda t0: abs(s - t0)))
        j = s
        while j < n - 1 and dd[j] < -0.25:
            j += 1
        if j > s:
            MAE.append(dd[s:j + 1].min() - dd[s])
print(f"真实周期{cyc_all}轮 | 入场事件{ev_all}次")
print(f"Bottom Recall  = {hit_c}/{cyc_all} = {hit_c / cyc_all * 100:.0f}%")
print(f"Precision      = {hit_e}/{ev_all} = {hit_e / ev_all * 100:.0f}%")
print(f"Timing Error   = 中位{np.median(TC):+.0f}日 均值{np.mean(TC):+.0f}日")
print(f"MAE            = 均值{np.mean(MAE) * 100:.1f}% 最差{np.min(MAE) * 100:.1f}%")
