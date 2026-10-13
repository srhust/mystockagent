# -*- coding: utf-8 -*-
"""V4专户同口径迁移验证 (用户指令): 9只ETF/同区间/0.1%双边/每专户预算=100/9固定, 闲置计净值。
配置: 1专户一次性 2专户30/35/35 3专户30/35/35+自适应回撤出场 4六槽共享池30/35/35 5专户30/35/35+temp75出场消融 6Baseline参照。
信号=行业指数(temp<12∨dd≤-max(0.30,0.70×最深)), 执行=ETF收盘。总账户=Σ专户净值。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from carriers import CARRIERS
from engine import run_specialist, run_ledger, metrics, fmt

ETF_COST = 0.001
CAP = 100.0
obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]


def fetch_etf(sym6):
    fp = os.path.join(config.DATA_DIR, f"etf_{sym6}.csv")
    return pd.read_csv(fp, parse_dates=["date"]).rename(columns={"收盘": "close"}).set_index("date")["close"].astype(float)


def temperature(c):
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)


idx_s, etf_s, names, sigs, exts = {}, {}, [], {}, {}
for sid, info in CARRIERS.items():
    if not (info["enabled"] and info["corr"] >= 0.85):
        continue
    hit = next((n for n in dfs if n == info["sector_name"] or info["sector_name"] in n), None)
    if not hit:
        continue
    c = dfs[hit]["close"].dropna()
    c.index = pd.to_datetime(c.index)
    c = c[c.index >= "2010-01-01"]
    if len(c) < 1250:
        continue
    t = temperature(c)
    dd = c / c.cummax() - 1.0
    dep = (-dd).rolling(1250, min_periods=250).max()
    thr = np.maximum(0.30, 0.70 * dep)
    sigs[sid] = (((t < 12) | (dd <= -thr)) & (t > 0)).astype(float)
    exts[sid] = {"t85": (t > 85).astype(float),
                 "t85dd": ((t > 85) | (dd >= -0.25)).astype(float),
                 "t75dd": ((t > 75) | (dd >= -0.25)).astype(float)}
    idx_s[sid] = c
    etf_s[sid] = fetch_etf(info["symbol"])
    names.append(sid)

cal = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for s in etf_s.values()])))
budget = CAP / len(names)
print(f"专户×{len(names)}, 每户预算{budget:.2f}, 成本{ETF_COST:.1%}/边\n")


def spec_run(which_ext, tranches):
    eqs, exps = [], []
    for n in names:
        sig = sigs[n].reindex(cal).fillna(0)
        ext = exts[n][which_ext].reindex(cal).fillna(0)
        px = etf_s[n].reindex(cal)
        eq, fills, _ = run_specialist(px, sig, ext, cal, budget=budget, cost=ETF_COST, tranches=tranches)
        eqs.append(eq)
        exps.append(eq.attrs.get("avg_exposure", float("nan")))
    tot = pd.concat(eqs, axis=1).sum(axis=1)
    return tot.dropna(), float(np.nanmean(exps))


print(f"{'配置':<34}{'CAGR':>8}{'Sharpe':>8}{'MDD':>8}{'恢复日':>7}{'平均暴露':>8}")
configs = [
    ("1 专户 一次性买入 (t85出场)", ("t85", None)),
    ("2 专户 30/35/35 (t85出场)", ("t85", (0.30, 0.35, 0.35))),
    ("3 专户 30/35/35 +回撤修复出场", ("t85dd", (0.30, 0.35, 0.35))),
    ("5 专户 30/35/35 (t75出场消融)", ("t75dd", (0.30, 0.35, 0.35))),
]
for tag, (e, tr) in configs:
    tot, exp = spec_run(e, tr)
    m = metrics(tot)
    print(f"{tag:<34}{m['CAGR']*100:>7.2f}%{m['Sharpe']:>8.2f}{m['MDD']*100:>7.1f}%{m['recovery_days']:>7}{exp*100:>7.1f}%")

# 4 六槽共享池 30/35/35
PX = pd.DataFrame({n: etf_s[n].reindex(cal) for n in names})
SIG = pd.DataFrame({n: sigs[n].reindex(cal).fillna(0) for n in names})
EXT = pd.DataFrame({n: exts[n]["t85dd"].reindex(cal).fillna(0) for n in names})
STRE = pd.DataFrame({n: -(idx_s[n] / idx_s[n].cummax() - 1).rolling(20).mean().reindex(cal).fillna(-9) for n in names})
res = run_ledger(PX, SIG, EXT, STRE, {n: n for n in names}, cal, max_pos=6, per_cat=2,
                 cost=ETF_COST, cap=CAP, tranches=True)
m = metrics(res["equity"].dropna())
print(f"{'4 六槽共享池 30/35/35':<34}{m['CAGR']*100:>7.2f}%{m['Sharpe']:>8.2f}{m['MDD']*100:>7.1f}%{m['recovery_days']:>7}")
res2 = run_ledger(PX, SIG, EXT, STRE, {n: n for n in names}, cal, max_pos=6, per_cat=2,
                  cost=ETF_COST, cap=CAP, tranches=False)
m2 = metrics(res2["equity"].dropna())
print(f"{'6 Baseline 六槽一次性 (参照)':<34}{m2['CAGR']*100:>7.2f}%{m2['Sharpe']:>8.2f}{m2['MDD']*100:>7.1f}%{m2['recovery_days']:>7}")
