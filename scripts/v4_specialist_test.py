# -*- coding: utf-8 -*-
"""V4 专户版: 30/35/35三档摊平 + (温度12或回撤-50)触发 + 85/75出场, 每行业独立账户。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

FIN = {"证券", "保险", "银行", "多元金融"}
obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]


def temp_series(c):
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)


rows = []
for name, d in dfs.items():
    cs = d["close"].dropna()
    cs.index = pd.to_datetime(cs.index)
    cs = cs[cs.index >= "2010-01-01"]
    if len(cs) < 1250:
        continue
    ext = 75 if name in FIN else 85
    t = temp_series(cs).values
    dd = (cs / cs.cummax() - 1.0).values
    cv, idx, n = cs.values, cs.index, len(cs)
    cash, units, cost, inpos, ei = 100.0, 0.0, 0.0, False, 0
    trades, holdsum = [], 0
    for i in range(n):
        if not inpos:
            if (np.isfinite(t[i]) and t[i] < 12) or (np.isfinite(dd[i]) and dd[i] <= -0.50):
                inpos, units, cost, ei = True, 30, cv[i], i
        else:
            avg = cost / units if units else 0
            if units < 100 and cv[i] <= avg * 0.85 and units < 65:
                units, cost = units + 35, cost + cv[i] * 35
            elif units < 100 and cv[i] <= avg * 0.70:
                units, cost = units + 35, cost + cv[i] * 35
            elif (np.isfinite(t[i]) and t[i] > ext) or (i - ei) > 1800:
                cash += units * (cv[i] - cost / units) / 100 + cost / units * units / 100 * 0
                pnl = units * cv[i] - cost
                cash += cost + pnl
                trades.append(pnl / 100)
                holdsum += i - ei
                inpos, units, cost = False, 0.0, 0.0
    dep = holdsum * 0.65 / 100
    tot = cash - 100
    ann = ((1 + tot / 100) ** (365.25 / (dep * 100 if dep > 1 else 1)) - 1) if dep > 1 else 0
    rows.append(dict(sector=name, n=len(trades), ret=tot, ann=ann * 100,
                     win=sum(1 for x in trades if x > 0), hold=holdsum / len(trades) if trades else 0))
R = pd.DataFrame(rows)
fin = R[R.sector.isin(FIN)]
oth = R[~R.sector.isin(FIN)]
for tag, sub in [("非金融", oth), ("金融", fin), ("全体", R)]:
    dep = sub["hold"].sum() * 0.65
    ann = ((1 + sub["ret"].sum() / 3000) ** (365.25 / dep) - 1) * 100 if dep else 0
    print(f"{tag}: 事件{sub['n'].sum()} 总收益{sub['ret'].sum():+.0f}%(30资金/行业) "
          f"在投年化{ann:+.1f}% 胜{sub['win'].sum()}/{sub['n'].sum()} 持仓{sub['hold'].mean():.0f}天")
