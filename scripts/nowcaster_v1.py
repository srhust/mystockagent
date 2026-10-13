# -*- coding: utf-8 -*-
"""审查十二轮: Nowcaster v1 — 跨存储股池化训练(MU/WDC/STX) + 扩展窗内isotonic校准 + 分组purge。
评价: 各股zigzag谷底召回@预警段, 假警/年, Brier/校准(修正过自信)。样本: MU 2009起 / STX 2002起 / WDC 2012起。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression

import config

STOCKS = {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv"}
DATA = {}
for name, fn in STOCKS.items():
    fp = os.path.join(config.DATA_DIR, "mu", fn)
    df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date").set_index("date")
    c = df["close"].astype(float)
    # 标准交替zigzag(审查十二轮: 极值点记录, 同日至多一个pivot): 谷底=反弹确认时的极低点
    hi_i = lo_i = 0
    hi = lo = c.iloc[0]
    trend, pivots = 0, []
    for i in range(1, len(c)):
        x = c.iloc[i]
        if trend >= 0:  # 上升腿: 追高; 回撤30%确认峰
            if x > hi:
                hi, hi_i = x, i
            elif x <= hi * 0.70:
                pivots.append(("H", hi_i))
                trend, lo, lo_i = -1, x, i
        else:  # 下降腿: 追低; 反弹30%确认谷
            if x < lo:
                lo, lo_i = x, i
            elif x >= lo * 1.30:
                pivots.append(("L", lo_i))
                trend, hi, hi_i = 1, x, i
    if trend < 0:
        pivots.append(("L", lo_i))  # 未完成下降腿的暂定谷
    troughs = sorted(set(i for k, i in pivots if k == "L"))
    DATA[name] = dict(close=c, troughs=troughs)
    print(f"{name}: {len(c)}日 {c.index[0].date()}~{c.index[-1].date()} 谷底{len(troughs)}个")

from bottom_nowcaster import build_labels, build_features

rows_X, rows_y, meta = [], [], {}
for name, d in DATA.items():
    f = build_features(d["close"], d["troughs"])
    lab = build_labels(d["close"], d["troughs"])
    meta[name] = dict(f=f, lab=lab, close=d["close"], troughs=d["troughs"])

TARGETS = ("P_bottom_20d", "P_reversal_60d", "P_upcycle_120d")
start = 1000
all_dates = sorted(set().union(*[set(meta[n]["close"].index) for n in meta]))
years = sorted(set(d.year for d in all_dates))
preds = {n: {t: pd.Series(np.nan, index=meta[n]["close"].index) for t in TARGETS} for n in meta}


def pooled_train(upto_date, purge=120):
    """池化: 所有股票 < upto_date-120d 的行(标签窗完整)。"""
    Xs, ys = {t: [] for t in TARGETS}, {t: [] for t in TARGETS}
    cut = upto_date - pd.Timedelta(days=purge * 1.6)
    for n in meta:
        f, lab = meta[n]["f"], meta[n]["lab"]
        m = f.index < cut
        X = f[m]
        for t in TARGETS:
            y = lab[t][m]
            k = np.isfinite(y) & np.isfinite(X).all(axis=1).values
            Xs[t].append(X[k])
            ys[t].append(y[k].astype(float))
    return Xs, ys


for y0 in years[2:]:
    refit_date = pd.Timestamp(f"{y0}-01-01")
    Xs, ys = pooled_train(refit_date)
    for t in TARGETS:
        if not Xs[t]:
            continue
        X = pd.concat(Xs[t])
        y = pd.concat(ys[t])
        ok = np.isfinite(X).all(axis=1).values & np.isfinite(y).values
        X, y = X[ok], y[ok]
        if len(X) < 800 or y.nunique() < 2:
            continue
        mu_, sd_ = X.mean(), X.std().replace(0, 1)
        lr = LogisticRegression(C=0.5, max_iter=1000).fit((X - mu_) / sd_, y)
        # 扩展窗内校准: 训练后半段 out-of-fold 拟合 isotonic
        half = len(X) // 2
        p_in = lr.predict_proba((X.iloc[half:] - mu_) / sd_)[:, 1]
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(p_in, y.iloc[half:].values)
        for n in meta:
            f = meta[n]["f"]
            seg = f[(f.index >= refit_date) & (f.index < pd.Timestamp(f"{y0+1}-01-01"))]
            seg = seg[np.isfinite(seg).all(axis=1).values]
            if len(seg) == 0:
                continue
            pz = lr.predict_proba((seg - mu_) / sd_)[:, 1]
            pc = iso.predict(pz)
            preds[n][t].loc[seg.index] = pc

print("\n=== v1 盲回放(池化+isotonic校准) ===")
for n in meta:
    d = meta[n]
    p = preds[n]["P_bottom_20d"]
    segs = []
    for i in range(len(p)):
        if np.isfinite(p.iloc[i]) and p.iloc[i] >= 0.5:
            if segs and i - segs[-1][-1] <= 5:
                segs[-1].append(i)
            else:
                segs.append([i])
    hits, miss = 0, []
    for t in d["troughs"]:
        cand = [s[0] for s in segs if t - 250 <= s[0] <= t + 60]
        if cand:
            hits += 1
        else:
            miss.append(str(d["close"].index[t].date()))
    zone = set()
    for t in d["troughs"]:
        zone.update(range(max(0, t - 10), min(len(d["close"]), t + 11)))
    fa = [s for s in segs if not any(k in zone for k in s)]
    yrs = (d["close"].index[-1] - d["close"].index[0]).days / 365.25
    print(f"{n}: 谷底召回 {hits}/{len(d['troughs'])} 预警段{len(segs)} 假警{len(fa)}({len(fa)/yrs:.1f}/年) 漏检谷底{miss[:4]}")
    for t in TARGETS:
        pr, y = preds[n][t].values, d["lab"][t].values
        m = np.isfinite(pr) & np.isfinite(y)
        if m.sum() < 100:
            continue
        brier = float(np.mean((pr[m] - y[m]) ** 2))
        cal_s = " ".join(f"[{lo:.1f},{lo+0.2:.1f})->{y[m & (pr>=lo)&(pr<lo+0.2)].mean():.2f}(n={(m&(pr>=lo)&(pr<lo+0.2)).sum()})" for lo in (0.2, 0.4, 0.6, 0.8) if (m & (pr >= lo) & (pr < lo + 0.2)).sum() >= 10)
        print(f"   {t}: Brier {brier:.3f} {cal_s}")

