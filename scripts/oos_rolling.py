# -*- coding: utf-8 -*-
"""滚动样本外验证: B策略(冻结规则) 分年度净值收益 + Sharpe/Calmar/回撤恢复时间。
规则冻结: temp<12 ∨ dd<=-max(0.30,0.7×1250日最深) 入场; temp>85 ∨ dd>=-0.25 出场; T+1收盘; 0.15%双边。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

COST, CAP = 0.0015, 100.0
obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]


def temperature(c):
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)


cols, catmap = {}, {}
for name, d in dfs.items():
    cs = d["close"].dropna()
    cs.index = pd.to_datetime(cs.index)
    cs = cs[cs.index >= "2010-01-01"]
    if len(cs) >= 1250:
        cols[name] = cs
        catmap[name] = meta.get(name, {}).get("cat", "其他")
cal = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for s in cols.values()])))
PX = pd.DataFrame(cols).reindex(cal)
TEMPS = pd.DataFrame({n: temperature(s).reindex(cal) for n, s in cols.items()})
DD = pd.DataFrame({n: (s / s.cummax() - 1).reindex(cal) for n, s in cols.items()})
DEP = (-DD).rolling(1250, min_periods=250).max()
THR = np.maximum(0.30, 0.70 * DEP)
SIG = (((TEMPS < 12) | (DD <= -THR)) & (TEMPS > 0)).astype(int)
EXT = ((TEMPS > 85) | (DD >= -0.25)).astype(int)
STRE = -DD.rolling(20).mean().reindex(cal)

cash, pos, last_px, stale, entry = CAP, {}, {}, {}, {}
eq = pd.Series(np.nan, index=cal)
eqmax, mdd_start = CAP, None
for i, d in enumerate(cal):
    mv, _mv = 0.0, 0.0
    for n, sh in pos.items():
        px = PX.at[d, n] if n in PX.columns else np.nan
        if np.isfinite(px):
            last_px[n], stale[n] = float(px), 0
        else:
            px = last_px.get(n)
            stale[n] = stale.get(n, 0) + 1
        if px is not None and np.isfinite(px):
            mv += sh * px
    eq.iloc[i] = cash + mv
    eqmax = max(eqmax, eq.iloc[i])
    sd = cal[i - 1] if i > 0 else None
    sell = [n for n in list(pos) if sd is not None and EXT.loc[sd, n] == 1] if i > 0 else []
    for n in sell:
        px = PX.at[d, n] if n in PX.columns else np.nan
        if np.isfinite(px) and stale.get(n, 0) <= 20:
            cash += pos[n] * px * (1 - COST)
            del pos[n], entry[n], last_px[n], stale[n]
    if i > 0:
        cand = [n for n in cols if n not in pos and SIG.loc[sd, n] == 1 and np.isfinite(PX.at[d, n])]
        cand.sort(key=lambda n: STRE.at[sd, n] if np.isfinite(STRE.at[sd, n]) else float("-inf"))
        cats = {}
        for n in pos:
            cats[catmap[n]] = cats.get(catmap[n], 0) + 1
        buys = []
        for n in cand:
            if len(buys) >= 6 - len(pos):
                break
            if cats.get(catmap[n], 0) >= 2:
                continue
            buys.append(n)
            cats[catmap[n]] = cats.get(catmap[n], 0) + 1
        for n in buys:
            px = PX.at[d, n]
            spend = min(cash * 0.999, eq.iloc[i] / 6)
            if spend > px * 0.001:
                cash -= spend
                pos[n] = pos.get(n, 0) + spend * (1 - COST) / px
                entry[n] = d

r = eq.pct_change().dropna()
yr = (1 + r).groupby(r.index.year).prod() - 1
sharpe = r.mean() / r.std() * np.sqrt(244) if r.std() > 0 else 0
dd = eq / eq.cummax() - 1
mdd = dd.min()
trough_i = dd.idxmin()
rec = eq[eq.index > trough_i]
rec_days = int(np.argmax((rec >= eq.loc[trough_i] / (1 + mdd) * (1 + mdd)).values)) if len(rec) else -1
# 恢复到前高
pre_max_i = eq[:trough_i].idxmax()
rec2 = eq[eq.index > trough_i]
rec_i = rec2[rec2 >= eq.loc[pre_max_i]].index
rec_days = (rec_i[0] - trough_i).days if len(rec_i) else -1
print("B策略冻结规则分年度净值收益:")
for y, v in yr.items():
    print(f"  {y}: {v * 100:+.1f}%")
print(f"Sharpe {sharpe:.2f} | Calmar {(eq.iloc[-1] / eq.iloc[0]) ** (365.25 / (eq.index[-1] - eq.index[0]).days) - 1:.4f}/{abs(mdd):.2f} | MDD {mdd * 100:.1f}% | 谷底{trough_i.date()} 恢复{rec_days}日")
half = eq[eq.index < "2018-01-01"]
print(f"IS 2010-2017: CAGR {((1+ (half.pct_change().dropna())).prod() ** (1/8) - 1) * 100:+.2f}%")
oos = eq[eq.index >= "2018-01-01"]
print(f"OOS 2018-2026: CAGR {((oos.iloc[-1] / oos.iloc[0]) ** (365.25 / (oos.index[-1] - oos.index[0]).days) - 1) * 100:+.2f}%")
