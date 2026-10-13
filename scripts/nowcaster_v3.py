# -*- coding: utf-8 -*-
"""审查十二轮v3: v2 + SEC免费产业特征(营收YoY/加速度/毛利率/存货比, as-of=提交日)。
仅2017+可用(ASC606), 早期行中性填充999标记缺失; 对照v2检验产业证据的增量。"""
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
    qual = [t for t in raw_tr if len(c.iloc[t + 1:t + 121]) >= 60 and float(c.iloc[t + 1:t + 121].max() / c.iloc[t] - 1) >= 0.50]
    # SEC产业特征(as-of=提交日): 当季值在filed日之后才可见
    fund = pd.read_csv(os.path.join(config.DATA_DIR, "mu", f"{name.lower()}_fund.csv"), parse_dates=["end", "filed"]).dropna(subset=["filed"])
    fund = fund.sort_values("filed")
    ind = pd.DataFrame(index=c.index)
    q = fund.drop_duplicates("filed", keep="last").set_index("filed")  # 审查十二轮: 同日多份去重
    for col in ("val", "gp", "inv"):
        if col in q.columns:
            ind[col] = q[col].reindex(c.index, method="ffill")
        else:
            ind[col] = np.nan  # STX无gp等: 列存在, 后续999填充
    ind["rev_yoy"] = ind["val"] / ind["val"].shift(4) - 1
    ind["rev_accel"] = ind["rev_yoy"] - ind["rev_yoy"].shift(1)
    ind["gm"] = ind["gp"] / ind["val"]
    ind["inv_rev"] = ind["inv"] / ind["val"]
    ind = ind[["rev_yoy", "rev_accel", "gm", "inv_rev"]].astype(float)
    ind["rev_yoy"] = ind["rev_yoy"].fillna(999.0)
    DATA[name] = dict(close=c, troughs=qual, ind=ind)
    print(f"{name}: 合格谷底{len(qual)} 产业特征{ind['rev_yoy'].ne(999).sum()}行有值(2017+)")

meta = {}
for name, d in DATA.items():
    f = build_features(d["close"], d["troughs"]).join(d["ind"], how="left")
    meta[name] = dict(f=f, lab=build_labels(d["close"], d["troughs"]), close=d["close"], troughs=d["troughs"])

TARGETS = ("P_bottom_20d", "P_reversal_60d", "P_upcycle_120d")
all_dates = sorted(set().union(*[set(meta[n]["close"].index) for n in meta]))
years = sorted(set(dd.year for dd in all_dates))
preds = {n: {t: pd.Series(np.nan, index=meta[n]["close"].index) for t in TARGETS} for n in meta}
PURGE = 190
for y0 in years[2:]:
    print(f"DBGYEAR {y0}")
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
        Xn = X.replace(999.0, np.nan).fillna(X.replace(999.0, np.nan).median())  # 审查十二轮: 中位数填充保留全部行
        ok = np.isfinite(Xn).all(axis=1).values & np.isfinite(y).values
        X2, y2 = Xn[ok], y[ok]
        if len(X2) < 800 or y2.nunique() < 2:
            print(f"DBGSkip {y0} {t} len={len(X2)} nun={y2.nunique()} nanY={int((~np.isfinite(y)).sum())}")
            continue
        mu_, sd_ = X2.mean(), X2.std().replace(0, 1)
        lr = LogisticRegression(C=0.5, max_iter=1000).fit((X2 - mu_) / sd_, y2)
        print(f"DBG {y0} {t} X2={len(X2)} pos={int(y2.sum())}")
        for n in meta:
            f, lab = train[n]
            fz = (f - mu_) / sd_
            fz = fz.replace(999.0, np.nan).fillna(Xn.median())
            fz = fz[np.isfinite(fz).all(axis=1).values]
            if len(fz) < 100:
                continue
            half = len(fz) // 2
            p_in = lr.predict_proba(fz.iloc[half:])[:, 1]
            y_in = lab[t].loc[fz.iloc[half:].index]
            k = np.isfinite(p_in) & np.isfinite(y_in.values)
            if k.sum() < 100 or len(np.unique(y_in.values[k])) < 2:
                continue
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(p_in[k], y_in.values[k].astype(float))
            seg = meta[n]["f"][(meta[n]["f"].index >= refit_date) & (meta[n]["f"].index < pd.Timestamp(f"{y0+1}-01-01"))]
            segn = seg.replace(999.0, np.nan).fillna(Xn.median())
            seg = segn[np.isfinite(segn).all(axis=1).values]
            if len(seg) == 0:
                continue
            preds[n][t].loc[seg.index] = iso.predict(lr.predict_proba((seg - mu_) / sd_)[:, 1])

print("\n=== v3 盲回放(+SEC产业特征) — 2017起评估(特征可得期) ===")
for n in meta:
    d = meta[n]
    p = preds[n]["P_bottom_20d"]
    pv = p[p.index >= "2017-06-01"].dropna()
    if len(pv) == 0:
        print(f"{n}: 无2017+预测"); continue
    thr = float(np.quantile(pv, 0.98))
    segs = []
    for i in range(len(p)):
        dt = p.index[i]
        if np.isfinite(p.iloc[i]) and p.iloc[i] >= thr and dt >= pd.Timestamp("2017-06-01"):
            if segs and i - segs[-1][-1] <= 5:
                segs[-1].append(i)
            else:
                segs.append([i])
    hits, leads, miss = 0, [], []
    for t in d["troughs"]:
        td = d["close"].index[t]
        if td < pd.Timestamp("2017-06-01"):
            continue
        cand = [s[0] for s in segs if t - 250 <= s[0] <= t + 60]
        if cand:
            hits += 1
            leads.append(cand[-1] - t)
        else:
            miss.append(str(td.date()))
    zone = set()
    for t in d["troughs"]:
        zone.update(range(max(0, t - 10), min(len(d["close"]), t + 11)))
    fa = [s for s in segs if not any(k in zone for k in s)]
    yrs = (d["close"].index[-1] - pd.Timestamp("2017-06-01")).days / 365.25
    print(f"{n} P98(thr={thr:.2f}): 2017+召回{hits} 假警{len(fa)}({len(fa)/yrs:.1f}/年) 提前中位{int(np.median(leads)) if leads else 'NA'}日 漏检{miss}")
    pr, yv = preds[n]["P_bottom_20d"].values, d["lab"]["P_bottom_20d"].values
    m = np.isfinite(pr) & np.isfinite(yv) & (d["close"].index >= "2017-06-01")
    if m.sum() > 100:
        print(f"   Brier {float(np.mean((pr[m]-yv[m])**2)):.3f} 校准: " + " ".join(f"[{lo:.1f},{lo+0.2:.1f})->{yv[m&(pr>=lo)&(pr<lo+0.2)].mean():.2f}(n={(m&(pr>=lo)&(pr<lo+0.2)).sum()})" for lo in (0.4, 0.6, 0.8) if (m & (pr >= lo) & (pr < lo + 0.2)).sum() >= 5))




