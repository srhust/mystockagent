# -*- coding: utf-8 -*-
"""审查二十轮: C v3归因 — 三因素分解(集中度/现金默认/评分窗口) + CAT分散相关性验证。
全部走同一真账本(run_ledger复用wf_v2设计): 现金+份额+15bp+T+1, 统一起点2013-01。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features

D = os.path.join(config.DATA_DIR, "mu")
POOL = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]
COST = 0.0015
closes, poss = {}, {}
for sym in POOL:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    ev = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    poss[sym] = pd.Series(ev.astype(float), index=c.index).rolling(120, min_periods=1).max().astype(float)
    closes[sym] = c
idx = pd.concat(closes.values()).index.unique().sort_values()
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in POOL}).ffill()
POS = pd.DataFrame({s: poss[s].reindex(idx) for s in POOL}).fillna(0.0)
MCLOSE = PX.resample("ME").last().dropna(how="all")
months = MCLOSE.index


def trailing_score(sym, m_end, win=36):
    w = MCLOSE[sym].loc[:m_end].dropna().iloc[-(win + 1):]
    if len(w) < win + 1 or w.iloc[0] <= 0:
        return np.nan
    eqw = w / w.iloc[0]
    mdd = float((eqw / eqw.cummax() - 1).min())
    return float(eqw.iloc[-1]) / abs(mdd) if mdd else np.nan


def build_schedule(win=36, active_only=True, splits=(0.6, 0.4)):
    sched = {}
    for m in [m for i, m in enumerate(months) if i >= 36]:
        t_day = idx[idx.to_period("M") == m.to_period("M")][0]
        scores, act = {}, {}
        for s in POOL:
            sc = trailing_score(s, m, win)
            if np.isfinite(sc):
                scores[s] = sc
                act[s] = bool(POS[s].reindex(idx[:idx.get_loc(t_day) + 1]).iloc[-1] >= 0.5)
        elig = {s: sc for s, sc in scores.items() if (act.get(s) or not active_only)}
        w = {}
        if elig:
            rank = sorted(elig, key=elig.get, reverse=True)
            for s, v in zip(rank, splits):
                if s is not None:
                    w[s] = v
        exec_day = idx[idx.get_loc(t_day) + 1] if idx.get_loc(t_day) + 1 < len(idx) else t_day
        sched[exec_day] = w
    return sched


def run_ledger(weight_schedule, start_day, budget=100.0):
    cash = float(budget)
    shares = {s: 0.0 for s in POOL}
    cur_w = {s: 0.0 for s in POOL}
    exec_days = set(weight_schedule)
    eq = pd.Series(np.nan, index=idx[idx.get_loc(start_day):])
    expo = pd.Series(np.nan, index=eq.index)
    start_i = idx.get_loc(start_day)
    for k, d in enumerate(eq.index):
        i = start_i + k
        sd = idx[i - 1] if i > 0 else d
        if d in exec_days:
            cur_w = dict(weight_schedule[d])
        px = PX.loc[d]
        port_val = cash + sum(shares[s] * px[s] for s in POOL)
        for s in POOL:
            target_v = cur_w.get(s, 0.0) * (POS[s].get(sd, 0.0)) * port_val
            cur_v = shares[s] * px[s]
            if abs(target_v - cur_v) > port_val * 1e-6 and np.isfinite(px[s]) and px[s] > 0:
                dv = target_v - cur_v
                cash -= dv + abs(dv) * COST
                shares[s] += dv / px[s]
        port_val = cash + sum(shares[s] * px[s] for s in POOL)
        eq.loc[d] = port_val
        expo.loc[d] = sum(shares[s] * px[s] for s in POOL) / port_val if port_val > 0 else 0.0
    return eq, expo


start_m = [m for i, m in enumerate(months) if i >= 36][12]
start_day = idx[idx.to_period("M") == start_m.to_period("M")][0]
print(f"统一起点: {start_day.date()}\n=== C v3 归因(基准=C v2: 36月评分/仅活跃/60-40) ===")
VARIANTS = {
    "C2基准(36月/活跃/60-40)": build_schedule(36, True, (0.6, 0.4)),
    "集中度: 100%单仓": build_schedule(36, True, (1.0,)),
    "集中度: 50-50": build_schedule(36, True, (0.5, 0.5)),
    "现金默认关闭(不看活跃)": build_schedule(36, False, (0.6, 0.4)),
    "评分窗: 12月": build_schedule(12, True, (0.6, 0.4)),
    "评分窗: 60月": build_schedule(60, True, (0.6, 0.4)),
}
for label, sched in VARIANTS.items():
    eq, expo = run_ledger(sched, start_day)
    mdd = float((eq / eq.cummax() - 1).min())
    print(f"{label}: 终值{float(eq.iloc[-1]):.1f} MDD{mdd:.0%} 暴露{float(expo.mean()):.0%} 比值{float(eq.iloc[-1])/abs(mdd):.0f}")

print("\n=== CAT分散验证: 月收益相关系数 ===")
mret = MCLOSE.pct_change().loc["2013-01":]
core = ["MU", "KLAC", "STX", "TSM"]
corr = mret[core + ["CAT"]].corr()
print(corr.round(2).to_string())
print(f"CAT与核心均值相关: {np.mean([corr.loc['CAT', s] for s in core]):.2f}")
planB = {"MU": 0.402, "KLAC": 0.228, "STX": 0.178, "TSM": 0.125, "CAT": 0.067}
planB_noCAT = {k: v / (1 - 0.067) for k, v in planB.items() if k != "CAT"}
for lbl, spec in [("B含CAT", planB), ("B去CAT(权重归一)", planB_noCAT)]:
    sched = {start_day: spec}
    eq, expo = run_ledger(sched, start_day)
    mdd = float((eq / eq.cummax() - 1).min())
    print(f"{lbl}: 终值{float(eq.iloc[-1]):.1f} MDD{mdd:.0%}")
