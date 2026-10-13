# -*- coding: utf-8 -*-
"""牧原股份(002714)温度周期专项: 深寒信号与收益。"""
import os

import numpy as np
import pandas as pd

fp = r"E:\mystockagent\data\stock_002714.csv"
df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
c = df.set_index("date")["close"].astype(float)
c = c[c.index >= "2014-02-01"]
lo = c.rolling(1250, min_periods=500).min()
hi = c.rolling(1250, min_periods=500).max()
pp = (c - lo) / (hi - lo)
r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
t = 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)
out = []
cv, idx, tv = c.values, t.index, t.values
n = len(tv)
for th_in, th_out in [(12, 75), (20, 75)]:
    i, trs = 0, []
    while i < n:
        if np.isfinite(tv[i]) and tv[i] < th_in:
            j = i
            while j < n and not (np.isfinite(tv[j]) and tv[j] > th_out):
                j += 1
            if j < n and j - i >= 5:
                trs.append((idx[i].date(), idx[j].date(), cv[j] / cv[i] - 1, j - i))
            i = j + 1
        else:
            i += 1
    rets = [x[2] for x in trs]
    out.append(f"temp<{th_in}出>{th_out}: {len(trs)}笔 " +
               " | ".join(f"{a}->{b} {r * 100:+.0f}%({d}天)" for a, b, r, d in trs) +
               (f" || 均值{np.mean(rets) * 100:+.0f}% 胜率{sum(r > 0 for r in rets)}/{len(rets)}" if rets else ""))
with open(r"E:\mystockagent\output\muyuan_study.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("DONE")
