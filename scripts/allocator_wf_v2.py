# -*- coding: utf-8 -*-
"""审查十九轮: walk-forward v2 — 四P0修复版。
①评分=36个月度观测(月收盘重采样), 窗口内收益重新归一化 ②A/B/C统一起点(C首个完整窗口月), 同初始100
③暴露=Σ持仓市值/权益(真实账本) ④真多标的账本: 现金+份额+15bp费+T+1(月度权重T日决策T+1执行; 日内策略仓位T-1决定T执行)。"""
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
    """窗口内归一化的月度观测评分: (末/首的窗口收益) / |窗口MDD(月收盘)|。"""
    w = MCLOSE[sym].loc[:m_end].dropna().iloc[-(win + 1):]
    if len(w) < win + 1 or w.iloc[0] <= 0:
        return np.nan
    eqw = w / w.iloc[0]  # 窗口内重新归一化
    mdd = float((eqw / eqw.cummax() - 1).min())
    return float(eqw.iloc[-1]) / abs(mdd) if mdd else np.nan

dec_months = [m for i, m in enumerate(months) if i >= 36]
weights_by_exec = {}
for m in dec_months:
    m_pos = MCLOSE.index.get_loc(m)
    t_day = idx[idx.to_period("M") == m.to_period("M")][0]  # 月初第一交易日(决策日)
    scores, active = {}, {}
    for s in POOL:
        sc = trailing_score(s, m)
        if np.isfinite(sc):
            scores[s] = sc
            active[s] = bool(POS[s].reindex(idx[:idx.get_loc(t_day) + 1]).iloc[-1] >= 0.5)  # 当前信号真实持仓中
    elig = {s: sc for s, sc in scores.items() if active.get(s)}
    if elig:
        best = max(elig, key=elig.get)
        second = max((s for s in elig if s != best), key=elig.get, default=None)
        w = {best: 0.6} | ({second: 0.4} if second else {})
    else:
        w = {}
    exec_day = idx[idx.get_loc(t_day) + 1] if idx.get_loc(t_day) + 1 < len(idx) else t_day  # T+1执行
    weights_by_exec[exec_day] = w
planA_scores = {}
for s in POOL:
    w = MCLOSE[s].dropna()
    if len(w) > 40:
        eqw = w / w.iloc[0]
        mdd = float((eqw / eqw.cummax() - 1).min())
        if mdd:
            planA_scores[s] = float(eqw.iloc[-1]) / abs(mdd)
tot = sum(planA_scores.values())
planA_full = {s: v / tot for s, v in planA_scores.items()}
planB_full = {"MU": 0.402, "KLAC": 0.228, "STX": 0.178, "TSM": 0.125, "CAT": 0.067}


def run_ledger(weight_schedule, start_day, budget=100.0):
    """真账本: 现金+份额; 月度权重在执行日T+1调仓至目标; 日内策略仓位POS(T-1)决定T日实际持仓。
    desired_i = w_i × POS_i(T-1); 目标市值变化即交易(15bp)。暴露=Σ持仓市值/权益。"""
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
        # 日度目标暴露 = 当前权重 × 策略仓位(昨收决定, T+1)
        px = PX.loc[d]
        port_val = cash + sum(shares[s] * px[s] for s in POOL)
        for s in POOL:
            target_v = cur_w.get(s, 0.0) * (POS[s].get(sd, 0.0)) * port_val
            cur_v = shares[s] * px[s]
            if abs(target_v - cur_v) > port_val * 1e-6 and np.isfinite(px[s]) and px[s] > 0:
                dv = target_v - cur_v
                fee = abs(dv) * COST
                cash -= dv + fee
                shares[s] += dv / px[s]
        port_val = cash + sum(shares[s] * px[s] for s in POOL)
        eq.loc[d] = port_val
        expo.loc[d] = sum(shares[s] * px[s] for s in POOL) / port_val if port_val > 0 else 0.0
    return eq, expo


start = dec_months[12]  # 评分窗36月+12月观测缓冲 → 统一起点
start_day = idx[idx.to_period("M") == start.to_period("M")][0]
print(f"统一起点: {start_day.date()} (C具36月评分窗+A/B同日重置100)")
plans = {
    "C_walkforward贪心v2": weights_by_exec,
    "A_静态全历史评分(含未来,参照)": {start_day: planA_full},
    "B_静态方案B": {start_day: planB_full},
}
for label, ws in plans.items():
    eq, expo = run_ledger(ws, start_day)
    mdd = float((eq / eq.cummax() - 1).min())
    print(f"{label}: 终值{float(eq.iloc[-1]):.1f} MDD{mdd:.0%} 真实平均暴露{float(expo.mean()):.0%} 比值{float(eq.iloc[-1])/abs(mdd):.0f}")
eqc, expoc = run_ledger(weights_by_exec, start_day)
eqc.to_csv(os.path.join(config.DATA_DIR, "allocator_wf_v2_equity.csv"))
wf_w = pd.DataFrame({d: w for d, w in weights_by_exec.items()}).T
wf_w.reindex(columns=POOL).fillna(0.0).to_csv(os.path.join(config.DATA_DIR, "allocator_wf_v2_weights.csv"))
print(f"C现金月度: 平均暴露{float(expoc.mean()):.0%}, 零暴露日占比{float((expoc < 0.01).mean()):.0%}; 月度权重明细已存allocator_wf_v2_weights.csv")
