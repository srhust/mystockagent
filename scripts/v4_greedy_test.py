# -*- coding: utf-8 -*-
"""V4 三锚测试(贪心模式): 每行业独立账户, 触发即满仓, 出场即清仓, 无槽位管理。
A/B: base(12/75) base85(12/85) dd(-50/-25) v4((12或dd-50)且商品<60%, 85出场)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]
FUTS = {}
for n, m in meta.items():
    if m.get("fut"):
        fp = os.path.join(config.DATA_DIR, f"fut_{m['fut']}.csv")
        if os.path.exists(fp):
            f = pd.read_csv(fp, parse_dates=["date"]).set_index("date")["close"].astype(float)
            FUTS[n] = f


def temp_series(c):
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)


def fut_pos(n, d):
    f = FUTS.get(n)
    if f is None:
        return 0.5
    w = f[f.index <= d]
    if len(w) < 500:
        return 0.5
    w = w.iloc[-1250:]
    return float((w.iloc[-1] - w.min()) / (w.max() - w.min())) if w.max() > w.min() else 0.5


def simulate(c, ent, ext, use_dd=False, use_fut=False, sname=""):
    t = temp_series(c)
    dd = c / c.cummax() - 1.0
    cv, tv, dv, idx = c.values, t.values, dd.values, c.index
    n, i, cash, peak, mdd = len(cv), 0, 1.0, 1.0, 0.0
    trades, hold = [], 0
    in_pos = False
    entry_px = 1.0
    while i < n:
        if not in_pos:
            sig = (np.isfinite(tv[i]) and tv[i] < 12)
            if use_dd and np.isfinite(dv[i]) and dv[i] <= -0.50:
                sig = True
            if sig and use_fut and fut_pos(sname, idx[i]) >= 0.60:
                sig = False
            if sig:
                in_pos, entry_px, entry_i = True, cv[i], i
        else:
            hold = i - entry_i
            out = (np.isfinite(tv[i]) and tv[i] > ext) or hold > 1500
            if use_dd and dv[i] >= -0.25:
                out = True
            if out:
                cash *= cv[i] / entry_px
                trades.append((cv[i] / entry_px - 1, hold))
                in_pos = False
        if in_pos:
            peak = max(peak, cv[i] / entry_px)
            mdd = min(mdd, cv[i] / entry_px / peak - 1)
        i += 1
    yrs = (idx[-1] - idx[0]).days / 365.25
    dep = sum(d for _, d in trades)
    ann = (cash ** (365.25 / dep) - 1) if dep else 0.0
    return dict(n=len(trades), ret=(cash - 1) * 100, ann=ann * 100,
                win=sum(1 for r, _ in trades if r > 0), hold=np.mean([d for _, d in trades]) if trades else 0)


rows = []
for name, d in dfs.items():
    cs = d["close"].dropna()
    cs.index = pd.to_datetime(cs.index)
    cs = cs[cs.index >= "2010-01-01"]
    if len(cs) < 1250:
        continue
    rows.append(dict(sector=name,
                     base=simulate(cs, 12, 75), base85=simulate(cs, 12, 85),
                     dd=simulate(cs, 12, 85, use_dd=True),
                     v4=simulate(cs, 12, 85, use_dd=True, use_fut=True, sname=name)))
R = pd.DataFrame(rows)
out = []
for k in ["base", "base85", "dd", "v4"]:
    sub = pd.DataFrame(R[k].tolist())
    dep = sub["hold"].sum()
    tot = sub["ret"].sum()
    ann_tot = ((1 + tot / 100) ** (365.25 / dep) - 1) * 100 if dep else 0
    out.append(f"{k}: {len(sub)}行业 事件{sub['n'].sum()} 组合总收益{tot:+.0f}% 在投年化{ann_tot:+.1f}% "
               f"胜率{sub['win'].sum()}/{sub['n'].sum()} 持仓{sub['hold'].mean():.0f}天")
txt = "\n".join(out)
open(os.path.join(config.OUTPUT_DIR, "v4_greedy.txt"), "w", encoding="utf-8").write(
    txt + "\n\n" + R[["sector", "base", "v4"]].to_string())
print(txt)
