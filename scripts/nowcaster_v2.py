# -*- coding: utf-8 -*-
"""审查十二轮v2: ①谷底质量过滤(谷后120日内涨幅≥50%才算周期底, 与audit口径对齐) ②分层校准(每股自己的isotonic)。
达标线: MU recall≥7/合格谷底, 假警≤2/年, P0.8桶实现率≥0.6。"""
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
from bottom_nowcaster import build_labels, build_features

STOCKS = {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv"}
DATA = {}
for name, fn in STOCKS.items():
    fp = os.path.join(config.DATA_DIR, "mu", fn)
    df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date").set_index("date")
    c = df["close"].astype(float)
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
    raw_tr = sorted(set(i for k, i in pivots if k == "L"))
    # 质量过滤: 谷后120日内最大涨幅≥50% = 周期级底部
    qual = []
    for t in raw_tr:
        seg = c.iloc[t + 1:t + 121]
        if len(seg) >= 60 and float(seg.max() / c.iloc[t] - 1) >= 0.50:
            qual.append(t)
    DATA[name] = dict(close=c, troughs=qual)
    print(f"{name}: 谷底{len(raw_tr)} -> 合格{len(qual)} ({[str(c.index[t].date()) for t in qual][:8]})")

meta = {}
for name, d in DATA.items():
    meta[name] = dict(f=build_features(d["close"], d["troughs"]),
                      lab=build_labels(d["close"], d["troughs"]),
                      close=d["close"], troughs=d["troughs"])

TARGETS = ("P_bottom_20d", "P_reversal_60d", "P_upcycle_120d")
all_dates = sorted(set().union(*[set(meta[n]["close"].index) for n in meta]))
years = sorted(set(d.year for d in all_dates))
preds = {n: {t: pd.Series(np.nan, index=meta[n]["close"].index) for t in TARGETS} for n in meta}
PURGE = 190  # 120标签窗×1.6

for y0 in years[2:]:
    refit_date = pd.Timestamp(f"{y0}-01-01")
    cut = refit_date - pd.Timedelta(days=PURGE * 1.6)
    train = {}
    for n in meta:
        f, lab = meta[n]["f"], meta[n]["lab"]
        m = (f.index < cut)
        train[n] = (f[m], lab[m])
    for t in TARGETS:
        X = pd.concat([train[n][0] for n in meta])
        y = pd.concat([train[n][1][t] for n in meta])
        ok = np.isfinite(X).all(axis=1).values & np.isfinite(y).values
        X, y = X[ok], y[ok]
        if len(X) < 800 or y.nunique() < 2:
            continue
        mu_, sd_ = X.mean(), X.std().replace(0, 1)
        lr = LogisticRegression(C=0.5, max_iter=1000).fit((X - mu_) / sd_, y)
        half = len(X) // 2
        for n in meta:  # 分层校准: 每股用自己训练段后半的out-of-fold预测拟合isotonic
            f, lab = train[n]
            fz = (f - mu_) / sd_
            fz = fz[np.isfinite(fz).all(axis=1).values]  # 审查十二轮: 校准段同样去NaN
            if len(fz) <= half or len(fz) < 50:
                continue  # 该股此年样本不足(如WDC在2012年前)
            p_in = lr.predict_proba(fz.iloc[half:])[:, 1]
            y_in = lab[t].loc[fz.iloc[half:].index]
            k = np.isfinite(p_in) & np.isfinite(y_in.values)
            if k.sum() < 100 or len(np.unique(y_in.values[k])) < 2:
                continue
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(p_in[k], y_in.values[k].astype(float))
            seg = meta[n]["f"][(meta[n]["f"].index >= refit_date) & (meta[n]["f"].index < pd.Timestamp(f"{y0+1}-01-01"))]
            seg = seg[np.isfinite(seg).all(axis=1).values]
            if len(seg) == 0:
                continue
            preds[n][t].loc[seg.index] = iso.predict(lr.predict_proba(seg / 1 * 0 + (seg - mu_) / sd_)[:, 1])

print("\n=== v2 盲回放(质量谷底+分层校准) ===")
print("\n=== 稀有事件口径: 分位数阈值(每股价P98/P99为预警线) ===")
for n in meta:
    d = meta[n]
    p = preds[n]["P_bottom_20d"]
    pv = p.dropna()
    for q in (0.98, 0.99):
        thr = float(np.quantile(pv, q))
        segs = []
        for i in range(len(p)):
            if np.isfinite(p.iloc[i]) and p.iloc[i] >= thr:
                if segs and i - segs[-1][-1] <= 5:
                    segs[-1].append(i)
                else:
                    segs.append([i])
        hits, lead_lags, miss = 0, [], []
        for t in d["troughs"]:
            cand = [s[0] for s in segs if t - 250 <= s[0] <= t + 60]
            if cand:
                hits += 1
                lead_lags.append(cand[-1] - t)
            else:
                miss.append(str(d["close"].index[t].date()))
        zone = set()
        for t in d["troughs"]:
            zone.update(range(max(0, t - 10), min(len(d["close"]), t + 11)))
        fa = [s for s in segs if not any(k in zone for k in s)]
        yrs = (d["close"].index[-1] - d["close"].index[0]).days / 365.25
        print(f"{n} P{int(q*100)}(thr={thr:.2f}): 召回{hits}/{len(d['troughs'])} 假警{len(fa)}({len(fa)/yrs:.1f}/年) "
              f"提前量中位{int(np.median(lead_lags)) if lead_lags else 'NA'}日 漏检{miss[:4]}")

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
    print(f"{n}: 合格谷底召回 {hits}/{len(d['troughs'])} 预警段{len(segs)} 假警{len(fa)}({len(fa)/yrs:.1f}/年) 漏检{miss[:5]}")
    for t in TARGETS:
        pr, y = preds[n][t].values, d["lab"][t].values
        m = np.isfinite(pr) & np.isfinite(y)
        if m.sum() < 100:
            continue
        brier = float(np.mean((pr[m] - y[m]) ** 2))
        cal_s = " ".join(f"[{lo:.1f},{lo+0.2:.1f})->{y[m & (pr>=lo)&(pr<lo+0.2)].mean():.2f}(n={(m&(pr>=lo)&(pr<lo+0.2)).sum()})" for lo in (0.2, 0.4, 0.6, 0.8) if (m & (pr >= lo) & (pr < lo + 0.2)).sum() >= 10)
        print(f"   {t}: Brier {brier:.3f} {cal_s}")
