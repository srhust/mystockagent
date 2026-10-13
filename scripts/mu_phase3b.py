# -*- coding: utf-8 -*-
"""审查六轮⑤: 真暴露匹配 — 扫一次性首仓比例对齐A(30/35/35)的平均暴露10.6%, 同暴露下对比。
A2 30%暴露仅8.3%不相等的问题由此修复。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from engine import run_specialist, metrics

BUDGET = 100.0
mu = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "MU.csv"), parse_dates=["date"]).sort_values("date").set_index("date")
c = mu["close"].astype(float)
cal = c.index
t_lo, t_hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
pp = (c - t_lo) / (t_hi - t_lo)
r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
temp = 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)
dd = c / c.cummax() - 1.0
dep = (-dd).rolling(1250, min_periods=250).max()
thr = np.maximum(0.30, 0.70 * dep)
v4 = (((temp < 12) | (dd <= -thr)) & (temp > 0)).astype(float)
ext85 = (temp > 85).astype(float)

print(f"{'配置':<26}{'CAGR':>8}{'Sharpe':>8}{'MDD':>8}{'平均暴露':>8}{'单位暴露':>9}")
rows = {}
for tag, tr in [("A 30/35/35", (0.30, 0.35, 0.35))] + [(f"一次性{int(f*100)}%", (f,)) for f in (0.38, 0.42, 0.45, 0.50)]:
    eq, fills, _ = run_specialist(c, v4.reindex(cal).fillna(0), ext85.reindex(cal).fillna(0), cal,
                                  budget=BUDGET, cost=0.001, tranches=tr)
    m = metrics(eq.dropna())
    exp = eq.attrs.get("avg_exposure", 0)
    per = m["CAGR"] / exp if exp > 0 else 0
    rows[tag] = (m, exp, per)
    print(f"{tag:<26}{m['CAGR']*100:>7.2f}%{m['Sharpe']:>8.2f}{m['MDD']*100:>7.1f}%{exp*100:>7.1f}%{per*100:>8.1f}%")
a_exp = rows["A 30/35/35"][1]
best = min(((k, abs(v[1] - a_exp)) for k, v in rows.items() if k != "A 30/35/35"), key=lambda x: x[1])
print(f"\n最接近A暴露的配置: {best[0]} (暴露差{best[1]*100:.1f}pp)")
print(f"同暴露对比: A CAGR {rows['A 30/35/35'][0]['CAGR']*100:.2f}% vs {best[0]} CAGR {rows[best[0]][0]['CAGR']*100:.2f}%")
