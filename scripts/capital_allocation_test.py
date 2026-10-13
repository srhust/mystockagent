# -*- coding: utf-8 -*-
"""Phase5/资金分配对照实验: 9只高保真ETF, V4入场+30/35/35+t85dd出场。
S1 静态六槽等权(Baseline参照, 已有5.96%) vs S2 动态机会分配(score加权, cap 35%, watch预留20%)。
S2账本: 共享现金池, 入场时按allocate()定预算, 30%首仓/35%×2加仓, 出场释放。T+1, 0.1%/边。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from carriers import CARRIERS
from engine import metrics
from capital_allocator import opportunity_score, allocate

COST, CAP = 0.001, 100.0
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


data = {}
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
    data[sid] = dict(px=fetch_etf(info["symbol"]), sig=(((t < 12) | (dd <= -thr)) & (t > 0)).astype(float),
                     ext=((t > 85) | (dd >= -0.25)).astype(float), temp=t, dd=dd, thr=thr)

cal = pd.DatetimeIndex(sorted(set().union(*[set(d["px"].index) for d in data.values()])))
names = list(data)


def run_dynamic():
    """动态分配账本: 逐日T+1执行。持仓: {sid: {budget, cash_left, shares, stage}}。"""
    cash, pos, eq = CAP, {}, pd.Series(np.nan, index=cal)
    for i, d in enumerate(cal):
        mv = 0.0
        for sid, p in pos.items():
            px = data[sid]["px"].get(d, np.nan)
            mv += p["shares"] * (px if np.isfinite(px) else p.get("last_px") or 0)
        eq.iloc[i] = cash + mv
        equity = eq.iloc[i]
        sd = cal[i - 1] if i > 0 else None
        # 出场
        for sid in [s for s in list(pos) if data[s]["ext"].get(sd, 0) >= 1]:
            px = data[sid]["px"].get(d, np.nan)
            if np.isfinite(px):
                cash += pos[sid]["shares"] * px * (1 - COST)
                del pos[sid]
        # 入场(动态预算)
        cands = {}
        for sid in names:
            if sid in pos or data[sid]["sig"].get(sd, 0) < 1:
                continue
            px = data[sid]["px"].get(d, np.nan)
            if not np.isfinite(px):
                continue
            sc = opportunity_score(float(data[sid]["temp"].get(sd, 50)), float(data[sid]["dd"].get(sd, 0)),
                                   float(data[sid]["thr"].get(sd, 0.3)))
            cands[sid] = sc
        n_watch = sum(1 for sid in names if float(data[sid]["temp"].get(sd, 50)) < 25)
        budgets = allocate(cands, n_watch, equity, cash)
        for sid, bud in budgets.items():
            px = float(data[sid]["px"].get(d))
            spend = min(bud * 0.30, cash)
            if spend > px * 0.001:
                fee = spend * COST
                cash -= spend
                pos[sid] = dict(budget=bud, shares=(spend - fee) / px, stage=0, last_px=px)
        # 加仓(逆势35%×2, 以T-1收盘确认)
        for sid, p in pos.items():
            px = data[sid]["px"].get(d, np.nan)
            sd2 = data[sid]["px"].index.get_indexer([sd])[0] if sd in data[sid]["px"].index else -1
            if np.isfinite(px) and p["stage"] < 2 and sd2 > 0:
                prev = float(data[sid]["px"].iloc[sd2 - 1])
                entry_px = p.get("entry_px")
                if entry_px is None:
                    p["entry_px"] = p["last_px"]
                    entry_px = p["entry_px"]
                if prev <= entry_px * (1 - 0.15 * (p["stage"] + 1) - 0.0 * p["stage"]):
                    spend = min(p["budget"] * 0.35, cash)
                    if spend > px * 0.001:
                        cash -= spend
                        p["shares"] += spend * (1 - COST) / px
                        p["stage"] += 1
    return eq.dropna()


eq1 = run_dynamic()
m = metrics(eq1)
print(f"S2 动态机会分配: CAGR {m['CAGR']*100:+.2f}% Sharpe {m['Sharpe']:.2f} MDD {m['MDD']*100:.1f}% 恢复{m['recovery_days']}日")
print("S1 静态六槽等权 (Baseline参照): CAGR +5.96% Sharpe 0.50 MDD -21.4% 恢复627日")
