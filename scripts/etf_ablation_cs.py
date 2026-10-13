# -*- coding: utf-8 -*-
"""全链路ETF消融 v2 (审查三轮修复):
①标签=first-touch triple-barrier(年化波动障碍) ②LR严格滚动OOS: expanding折逐年前推,
训练集剔除末端120日标签不可观察样本(purge至6/30), 测试年分数全部为真样本外, 训练期无分数->无信号
③覆盖层权重显式index=cal, 引擎缺失权重报错 ④覆盖层=基线全仓+C浅调(过ranker)半仓。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

import config
from carriers import CARRIERS
from engine import run_ledger, metrics
from reversal_detector import detect, effective_start

ETF_COST = 0.001
obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]
ref = pd.read_csv(os.path.join(config.DATA_DIR, "idx_hs300.csv"), parse_dates=["date"]).set_index("date")["close"].astype(float)


def fetch_etf(sym6):
    fp = os.path.join(config.DATA_DIR, f"etf_{sym6}.csv")
    return pd.read_csv(fp, parse_dates=["date"]).rename(columns={"收盘": "close"}).set_index("date")["close"].astype(float)


def temperature(c):
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)


pairs = []
for sid, info in CARRIERS.items():
    if info["enabled"] and info["corr"] >= 0.85:
        hit = next((n for n in dfs if n == info["sector_name"] or info["sector_name"] in n), None)
        if hit and len(dfs[hit]["close"].dropna()) >= 1250:
            pairs.append((hit, sid, info["symbol"]))

# ---------- 候选特征池(全部行业, 不筛标签; 测试年无需标签即可打分) ----------
rowsF, rowsMeta = [], []
for name, d in dfs.items():
    c = d["close"].dropna()
    c.index = pd.to_datetime(c.index)
    c = c[c.index >= "2010-01-01"]
    if len(c) < 1250:
        continue
    br = d["breadth"].reindex(c.index) if "breadth" in d else None
    dd252 = c / c.rolling(252, min_periods=120).max() - 1.0
    rs = c / ref.reindex(c.index)
    amt = d["amount"].reindex(c.index) if "amount" in d else pd.Series(np.nan, index=c.index)
    F = pd.DataFrame({
        "temp": temperature(c), "dd252": dd252, "rs20": rs.pct_change(20),
        "rs_long": rs / rs.rolling(252, min_periods=120).mean() - 1.0,
        "bd20": (br - br.shift(20)) if br is not None else 0.0,
        "breadth": br if br is not None else 0.5,
        "vol_ratio": amt.rolling(20).mean() / amt.rolling(60).mean(),
        "pos3y": c.rolling(756, min_periods=300).rank(pct=True)})
    cand = (((dd252 <= -0.25) | ((dd252 > -0.25) & (dd252 <= -0.08) & (F["rs_long"] > -0.15)))
            & (c > c.rolling(20).max().shift(1)))
    lab = effective_start(c)
    for t in np.where(cand.values)[0]:
        row = F.iloc[t]
        if row.isna().any():
            continue
        rowsF.append(row.values.astype(float))
        rowsMeta.append((name, str(c.index[t].date()), lab.values[t]))
XF = np.array(rowsF)
meta = rowsMeta
dates = np.array([m[1] for m in meta])
yv = np.array([np.nan if m[2] is None or not np.isfinite(m[2]) else m[2] for m in meta])
print(f"候选池: {len(meta)}行 (标签可用 {np.isfinite(yv).sum()})")

# ---------- 严格滚动OOS: 测试年Y, 训练<Y-1-07-01且标签可观察 ----------
score = np.full(len(meta), np.nan)
taus = {}
aucs = []
for Y in range(2019, 2027):
    tr = [i for i in range(len(meta)) if dates[i] < f"{Y-1}-07-01" and np.isfinite(yv[i])]
    te = [i for i in range(len(meta)) if dates[i][:4] == str(Y)]
    if len(tr) < 100 or len(te) < 10:
        continue
    mu, sd = XF[tr].mean(0), XF[tr].std(0) + 1e-9
    m = LogisticRegression(max_iter=1000, C=0.5).fit((XF[tr] - mu) / sd, yv[tr])
    p_tr = m.predict_proba((XF[tr] - mu) / sd)[:, 1]
    p_te = m.predict_proba((XF[te] - mu) / sd)[:, 1]
    score[te] = p_te
    tau = float(np.quantile(p_tr, 0.7))
    taus[Y] = tau
    ok = [k for k in range(len(te)) if np.isfinite(yv[te[k]])]
    if len(ok) > 20 and len(set(yv[[te[k] for k in ok]])) > 1:
        aucs.append((Y, roc_auc_score(yv[[te[k] for k in ok]], p_te[ok])))
    hi = p_te >= tau
    prec_hi = float(np.nanmean(np.where(hi, yv[te], np.nan))) if hi.any() else float("nan")
    print(f"折{Y}: train={len(tr)} test={len(te)} tau={tau:.3f} OOS_AUC={aucs[-1][1] if aucs else float('nan'):.3f} top级精度={prec_hi:.2f} (base={np.nanmean(yv[te]):.2f})")
print("滚动OOS AUC:", [f"{y}:{a:.3f}" for y, a in aucs])

# ---------- 三配置ETF回测 ----------
idx_series, etf_series, sigs = {}, {}, {}
names = []
for base, sid, sym6 in pairs:
    d = dfs[base]
    c = d["close"].dropna()
    c.index = pd.to_datetime(c.index)
    c = c[c.index >= "2010-01-01"]
    idx_series[sid] = c
    etf_series[sid] = fetch_etf(sym6)
    names.append(sid)
    br = d["breadth"].reindex(c.index) if "breadth" in d else None
    det = detect(c, ref.reindex(c.index), br, d["amount"].reindex(c.index) if "amount" in d else None)
    t = temperature(c)
    dd = c / c.cummax() - 1.0
    dep = (-dd).rolling(1250, min_periods=250).max()
    thr = np.maximum(0.30, 0.70 * dep)
    base_sig = (((t < 12) | (dd <= -thr)) & (t > 0)).astype(float)
    cs_sig = det["start"].astype(float)
    # OOS分数映射回该行业序列(训练年无分->无信号)
    sub = [(i, m) for i, m in enumerate(meta) if m[0] == base]
    p_s = pd.Series(np.nan, index=c.index)
    for i, m in sub:
        if np.isfinite(score[i]):
            p_s.loc[pd.Timestamp(m[1])] = score[i]
    csr_sig = cs_sig * (p_s >= pd.Series(p_s).map(lambda v: np.nan)).astype(float)  # placeholder replaced below
    # tau按日所属折
    tau_of_day = pd.Series(np.nan, index=c.index)
    for Y, tv in taus.items():
        tau_of_day[(c.index.year == Y)] = tv
    csr_sig = cs_sig * ((p_s >= tau_of_day) & np.isfinite(p_s)).astype(float)
    sigs[sid] = (base_sig, cs_sig, csr_sig)

cal = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for s in etf_series.values()])))
PX = pd.DataFrame({n: etf_series[n].reindex(cal) for n in names})
catmap = {n: n for n in names}


def ext_of(n):
    s = idx_series[n]
    t = temperature(s)
    dd = s / s.cummax() - 1.0
    return ((t > 85) | (dd >= -0.25)).astype(float).reindex(cal).fillna(0)


EXT = pd.DataFrame({n: ext_of(n) for n in names})
STRE = pd.DataFrame({n: -(idx_series[n] / idx_series[n].cummax() - 1).rolling(20).mean().reindex(cal).fillna(-9) for n in names})
print(f"\n{'配置':<28}{'CAGR':>8}{'Sharpe':>8}{'MDD':>8}{'恢复日':>7}")
for tag, which, W in [("Baseline 温度+回撤", 0, None),
                      ("CycleStart 三路径", 1, None),
                      ("CS+ranker(严格OOS)", 2, None),
                      ("覆盖层:基线+C浅调半仓(OOS)", "ovl", True)]:
    if which == "ovl":
        SIG = pd.DataFrame({n: ((sigs[n][0] > 0) | (sigs[n][2] > 0)).astype(float).reindex(cal).fillna(0) for n in names})
        wdict = {n: np.where(sigs[n][0].reindex(cal).fillna(0) > 0, 1.0,
                             np.where(sigs[n][2].reindex(cal).fillna(0) > 0, 0.5, 0.0)) for n in names}
        Wdf = pd.DataFrame(wdict, index=cal)  # 修复: 显式日期索引
        res = run_ledger(PX, SIG, EXT, STRE, catmap, cal, max_pos=6, per_cat=2, cost=ETF_COST, sig_weight=Wdf)
    else:
        SIG = pd.DataFrame({n: sigs[n][which].reindex(cal).fillna(0) for n in names})
        res = run_ledger(PX, SIG, EXT, STRE, catmap, cal, max_pos=6, per_cat=2, cost=ETF_COST)
    m = metrics(res["equity"].dropna())
    print(f"{tag:<28}{m['CAGR']*100:>7.2f}%{m['Sharpe']:>8.2f}{m['MDD']*100:>7.1f}%{m['recovery_days']:>7}")
