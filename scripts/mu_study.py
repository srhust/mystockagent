# -*- coding: utf-8 -*-
"""美光(MU)专项: 温度序列 + 全部深寒信号及收益。"""
import os

import numpy as np
import pandas as pd

fp = r"E:\mystockagent\data\us_MU.csv"
df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
c = df.set_index("date")["close"].astype(float)
c = c[c.index >= "2010-01-01"]
lo = c.rolling(1250, min_periods=500).min()
hi = c.rolling(1250, min_periods=500).max()
pp = (c - lo) / (hi - lo)
r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
t = 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)
out = []
for th_in, th_out in [(12, 75), (20, 75), (12, 85)]:
    tv = t.values
    idx = t.index
    cv = c.values
    i, n = 0, len(tv)
    trs = []
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
with open(r"E:\mystockagent\output\mu_study.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("DONE")
