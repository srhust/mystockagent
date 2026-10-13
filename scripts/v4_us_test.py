# -*- coding: utf-8 -*-
"""V4 回撤锚美股泛化: 15只美股周期股, dd-50入场/-25修复出场。"""
import os

import numpy as np
import pandas as pd

DATA = r"E:\mystockagent\data"
SYMS = ["XLE", "XME", "COPX", "GDX", "XLF", "FCX", "NUE", "X", "CLF", "AA", "MOS", "NEM", "CAT", "MU"]
rows = []
for s in SYMS:
    fp = os.path.join(DATA, f"us_{s}.csv")
    if not os.path.exists(fp):
        continue
    df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date")
    c = df.set_index("date")["close"].astype(float)
    c = c[c.index >= "2010-01-01"]
    if len(c) < 750:
        continue
    dd = (c / c.cummax() - 1.0).values
    cv, idx = c.values, c.index
    n, i, cash, trades = len(cv), 0, 1.0, []
    inp = False
    while i < n:
        if not inp and np.isfinite(dd[i]) and dd[i] <= -0.50:
            inp, ep, ei = True, cv[i], i
        elif inp:
            if dd[i] >= -0.25 or (i - ei) > 1500:
                cash *= cv[i] / ep
                trades.append((cv[i] / ep - 1, i - ei))
                inp = False
        i += 1
    dep = sum(d for _, d in trades)
    ann = (cash ** (365.25 / dep) - 1) if dep else 0.0
    rows.append(f"{s}: {len(trades)}笔 {cash * 100 - 100:+.0f}% 在投年化{ann * 100:+.1f}% "
                f"胜率{sum(1 for r, _ in trades if r > 0)}/{len(trades)} 持仓{np.mean([d for _, d in trades]) if trades else 0:.0f}天")
tot_ann = [r for r in rows]
print("\n".join(tot_ann))
