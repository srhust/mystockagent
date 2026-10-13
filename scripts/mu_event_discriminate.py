# -*- coding: utf-8 -*-
"""审查十四轮: 拐点判别精度 — MU 62个企稳事件(5真57假)逐事件特征对比, 寻找as-of可得的判别阈值。
特征: dd252深度/距252日高点天数/自60日低点反弹幅度/MA20斜率/收盘vs MA50。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features

c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "MU.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
f = build_features(c, [])
dd = f["dd252"]
ma20 = c.rolling(20).mean()
ma50 = c.rolling(50).mean()
low60 = c.rolling(60).min()
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
s2 = ((c > ma20) & (ma20 > ma20.shift(5)) & state250).values
events, last = [], -99
for i in range(len(c)):
    if s2[i] and i - last >= 20:
        events.append(i)
        last = i
# 合格谷
hi_i = lo_i = 0
hi = lo = c.iloc[0]
trend, pivots = 0, []
for i in range(1, len(c)):
    x = c.iloc[i]
    if trend >= 0:
        if x > hi:
            hi, hi_i = x, i
        elif x <= hi * 0.70:
            pivots.append(("H", hi_i))
            trend, lo, lo_i = -1, x, i
    else:
        if x < lo:
            lo, lo_i = x, i
        elif x >= lo * 1.30:
            pivots.append(("L", lo_i))
            trend, hi, hi_i = 1, x, i
if trend < 0:
    pivots.append(("L", lo_i))
qual = [t for t in sorted(set(i for k, i in pivots if k == "L"))
        if len(c.iloc[t + 1:t + 121]) >= 60 and float(c.iloc[t + 1:t + 121].max() / c.iloc[t] - 1) >= 0.50]
used = set()
rows = []
for w in events:
    ts = [t for t in qual if t - 10 <= w <= t + 60 and t not in used]
    tag = bool(ts)
    if ts:
        used.add(min(ts))
    rows.append(dict(date=c.index[w].date(), w=w, true=tag,
                     dd=float(dd.iloc[w]), dsh=float(f["decline_days"].iloc[w]),
                     reb=float(c.iloc[w] / low60.iloc[w] - 1), sl20=float((ma20.iloc[w] / ma20.iloc[w - 5] - 1)),
                     v50=float(c.iloc[w] / ma50.iloc[w] - 1)))
d = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print(d.to_string(index=False))
print("\n真(n=%d) vs 假(n=%d) 中位对比:" % (d.true.sum(), (~d.true).sum()))
for col in ("dd", "dsh", "reb", "sl20", "v50"):
    print(f"  {col}: 真={d[d.true][col].median():.3f} 假={d[~d.true][col].median():.3f}")
