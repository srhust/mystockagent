# -*- coding: utf-8 -*-
"""锚②v2验证: 商品高位组按动量劈分。规则: 高位+动量向上=放行(趋势侧), 高位+动量向下=拦截(顶部区)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

meta = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))["meta"]
SEC2FUT = {n: m.get("fut") for n, m in meta.items() if m.get("fut")}
ev = pd.read_csv(os.path.join(config.OUTPUT_DIR, "commodity_anchor_events.csv"), parse_dates=["date"])
ev = ev[ev["sector"].isin(SEC2FUT)].dropna(subset=["fut_pos"]).copy()
mom20, brk = [], []
for _, r in ev.iterrows():
    f = pd.read_csv(os.path.join(config.DATA_DIR, f"fut_{SEC2FUT[r['sector']]}.csv"), parse_dates=["date"]).set_index("date")["close"].astype(float)
    f = f[f.index <= r["date"]]
    if len(f) < 60:
        mom20.append(np.nan); brk.append(np.nan); continue
    mom20.append(float(f.iloc[-1] / f.iloc[-21] - 1))
    brk.append(float(f.iloc[-1] / f.iloc[-61:].max() - 1) + 1)  # 1=创60日新高
ev["mom20"], ev["brk60"] = mom20, brk
g_low = ev[ev.fut_pos < 0.6]
g_hi = ev[ev.fut_pos >= 0.6]
g_up = g_hi[(g_hi.mom20 > 0) | (g_hi.brk60 >= 0.999)]
g_dn = g_hi[(g_hi.mom20 <= 0) & (g_hi.brk60 < 0.999)]
for tag, g in [("商品低位<60%", g_low), ("高位+动量向上/破60日高", g_up), ("高位+动量向下", g_dn), ("高位全体(旧锚②拦截)", g_hi)]:
    if len(g) < 5:
        print(f"{tag}: n={len(g)} 样本不足")
        continue
    print(f"{tag}: n={len(g)} 250日前向 均值{g['f250'].mean() * 100:+.1f}% 中位{g['f250'].median() * 100:+.1f}% 胜率{(g['f250'] > 0).mean() * 100:.0f}%")
