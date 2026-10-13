# -*- coding: utf-8 -*-
"""V4 专户版(份额法记账, 修正版): 30/35/35三档摊平 + (温度12或回撤-50)触发 + 85/75出场。"""
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
    ext = 75.0 if name in FIN else 85.0
    t = temp_series(cs).values
    dd = (cs / cs.cummax() - 1.0).values
    cv, n = cs.values, len(cs)
    cash, shares, cost_paid, inpos, ei = 100.0, 0.0, 0.0, False, 0
    trades, holdsum, inv_days = [], 0, 0.0
    for i in range(n):
        if not inpos:
            if (np.isfinite(t[i]) and t[i] < 12) or (np.isfinite(dd[i]) and dd[i] <= -0.50):
                inpos, ei = True, i
                cash -= 30.0
                shares += 30.0 / cv[i]
                cost_paid = 30.0
        else:
            avg = cost_paid / shares if shares else 0.0
            if cost_paid < 65.0 and cv[i] <= avg * 0.85:
                spend = 35.0
            elif cost_paid < 100.0 and cv[i] <= avg * 0.70:
                spend = 100.0 - cost_paid
            else:
                spend = 0.0
            if spend and cash >= spend - 1e-9:
                cash -= spend
                shares += spend / cv[i]
                cost_paid += spend
            inv_days += cost_paid  # 元·日
            if (np.isfinite(t[i]) and t[i] > ext) or (i - ei) > 1800:
                pnl = shares * cv[i] - cost_paid
                cash += shares * cv[i]
                trades.append(pnl)
                holdsum += i - ei
                inpos, shares, cost_paid = False, 0.0, 0.0
    dep_years = inv_days / 100.0 / 365.25
    tot = sum(trades)
    ann = ((1 + tot / 100.0) ** (1.0 / dep_years) - 1) * 100 if dep_years > 0.5 and tot > -100 else 0.0
    rows.append(dict(sector=name, n=len(trades), ret=tot, ann=ann,
                     win=sum(1 for x in trades if x > 0), hold=holdsum / len(trades) if trades else 0))
R = pd.DataFrame(rows)
fin, oth = R[R.sector.isin(FIN)], R[~R.sector.isin(FIN)]
for tag, sub in [("非金融", oth), ("金融", fin), ("全体", R)]:
    dep = (sub["ann"].astype(float) * 0 + 0).sum()  # placeholder no-op
    tot, nd = sub["ret"].sum(), sub["n"].sum()
    # 在投年化(账户层聚合): (1+总收益/100账户)^(1/占用年)-1, 占用年=Σ持仓×0.65近似
    depy = sub["hold"].sum() * 0.65 / 365.25
    ann2 = ((1 + tot / 3000) ** (1 / depy) - 1) * 100 if depy > 0 else 0
    print(f"{tag}: 事件{nd} 总收益{tot:+.0f}%(每行业100账户) 在投年化{ann2:+.1f}% "
          f"胜{int(sub['win'].sum())}/{nd} 平均持仓{sub['hold'].mean():.0f}天")
