# -*- coding: utf-8 -*-
"""全链路ETF消融: Baseline(temp12∨自适应回撤) vs CycleStart三路径 vs +ranker门控。
信号=行业指数计算, 执行=9只高保真ETF(0.1%/边), 引擎=engine.run_ledger。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from carriers import CARRIERS
from engine import run_ledger, metrics, fmt
from reversal_detector import detect
from cycle_ranker import build_dataset, fit_lr, score_proba, FEATURES

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
print("载体:", [(a, c) for a, s, c in pairs])

# ranker(冻结: train<=2019, 阈值=train分位70%)
X, y, meta = build_dataset(dfs, ref)
rk = fit_lr(X, y, meta)
tr_idx = [i for i, (_, dt) in enumerate(meta) if dt < "2020-01-01"]
tau = float(np.quantile(score_proba(rk["model"], rk["mu"], rk["sd"], X[tr_idx]), 0.7))
print(f"ranker: test AUC {rk['auc_test']:.3f}, 门控阈值 tau={tau:.3f}")

idx_series, etf_series, names = {}, {}, []
sig_frames = {k: {} for k in ("base", "cs", "csr")}
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
    # ranker分数(仅候选日有分)
    csr = cs_sig.copy()
    dd252 = c / c.rolling(252, min_periods=120).max() - 1.0
    rs_full = c / ref.reindex(c.index)
    amt = d["amount"].reindex(c.index) if "amount" in d else pd.Series(np.nan, index=c.index)
    F = pd.DataFrame({"temp": t, "dd252": dd252,
                      "rs20": rs_full.pct_change(20),
                      "rs_long": rs_full / rs_full.rolling(252, min_periods=120).mean() - 1.0,
                      "bd20": (br - br.shift(20)) if br is not None else 0.0,
                      "breadth": br if br is not None else 0.5,
                      "vol_ratio": amt.rolling(20).mean() / amt.rolling(60).mean(),
                      "pos3y": c.rolling(756, min_periods=300).rank(pct=True)})
    cand_mask = cs_sig.values > 0
    if cand_mask.any():
        Xs = F.iloc[np.where(cand_mask)[0]].values.astype(float)
        ok = ~np.isnan(Xs).any(axis=1)
        p = np.full(len(c), np.nan)
        p[np.where(cand_mask)[0][ok]] = score_proba(rk["model"], rk["mu"], rk["sd"], Xs[ok])
        csr = cs_sig * pd.Series((p >= tau) & np.isfinite(p), index=c.index).astype(float)
    sig_frames["base"][sid] = base_sig
    sig_frames["cs"][sid] = cs_sig
    sig_frames["csr"][sid] = csr

cal = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for s in etf_series.values()])))
PX = pd.DataFrame({n: etf_series[n].reindex(cal) for n in names})
catmap = {n: n for n in names}
print(f"\n{'配置':<22}{'CAGR':>8}{'Sharpe':>8}{'MDD':>8}{'恢复日':>7}")
for tag, key in [("Baseline 温度+回撤", "base"), ("CycleStart 三路径", "cs"), ("CycleStart+ranker", "csr")]:
    SIG = pd.DataFrame({n: sig_frames[key][n].reindex(cal).fillna(0) for n in names})
    EXT = pd.DataFrame({n: ((idx_series[n].pipe(lambda s: 100 * (0.60 * ((s - s.rolling(1250, min_periods=500).min()) / (s.rolling(1250, min_periods=500).max() - s.rolling(1250, min_periods=500).min())) + 0.25 * s.pct_change(120).rolling(1250, min_periods=500).rank(pct=True) + 0.15 * s.pct_change(60).rolling(1250, min_periods=500).rank(pct=True))) > 85) | (s / s.cummax() - 1 >= -0.25)).astype(float).reindex(cal).fillna(0) for n, s in idx_series.items()})
    STRE = pd.DataFrame({n: -(idx_series[n] / idx_series[n].cummax() - 1).rolling(20).mean().reindex(cal).fillna(-9) for n in names})
    res = run_ledger(PX, SIG, EXT, STRE, catmap, cal, max_pos=6, per_cat=2, cost=ETF_COST)
    m = metrics(res["equity"].dropna())
    print(f"{tag:<22}{m['CAGR']*100:>7.2f}%{m['Sharpe']:>8.2f}{m['MDD']*100:>7.1f}%{m['recovery_days']:>7}")
