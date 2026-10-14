# -*- coding: utf-8 -*-
"""审查十八轮: 机会驱动分配器 walk-forward — 每月初决策: 仅用截至T日的36月滚动S2评分(终值/|MDD|,月度序列近似)
+ 当前信号活跃(pos=1), 贪心配置(最佳活跃机会60%, 次优40%; 无活跃=现金)。与静态A(全历史评分)/B(固定40/23/18/13/7)同账本对比。"""
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
pos_d, ret_d = {}, {}
for sym in POOL:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    ev = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    pos = pd.Series(ev.astype(float), index=c.index).rolling(120, min_periods=1).max().astype(float)
    pos_d[sym] = pos
    ret_d[sym] = c.pct_change().fillna(0.0)
idx = pd.concat([ret_d[s] for s in POOL]).index.unique().sort_values()
# 月度评分序列: 每标的月末累计对数权益 → 36月窗口终值/|MDD|
log_eq = {}
for sym in POOL:
    le = (1 + pos_d[sym].shift(1).fillna(0) * ret_d[sym]).cumprod().apply(np.log)
    log_eq[sym] = le.reindex(idx).ffill().fillna(0.0)
months = idx.to_period("M").unique()
mstart = {p: idx[idx.to_period("M") == p][0] for p in months}
weights_hist = {}
planA_full = {s: None for s in POOL}  # 静态A稍后用全历史分
for p in months:
    if p.ordinal < months[0].ordinal + 36:
        continue
    t = mstart[p]
    scores, active = {}, {}
    for s in POOL:
        le = log_eq[s]
        w = le.loc[:t].iloc[-37:]
        if len(w) < 30:
            continue
        eqw = np.exp(w)
        mdd = float((eqw / eqw.cummax() - 1).min())
        scores[s] = float(eqw.iloc[-1] / 100.0) / abs(mdd) if mdd else 0.0
        active[s] = bool(pos_d[s].shift(1).reindex(idx[:idx.get_loc(t) + 1]).iloc[-1] >= 0.5) or bool(pos_d[s].reindex(idx[:idx.get_loc(t) + 1]).iloc[-20:].max() >= 0.5)
    elig = {s: sc for s, sc in scores.items() if active.get(s)}
    if elig:
        best = max(elig, key=elig.get)
        second = max((s for s in elig if s != best), key=elig.get, default=None)
        w = {best: 0.6} | ({second: 0.4} if second else {})
    else:
        w = {}
    weights_hist[t] = w
wf_w = pd.DataFrame({t: w for t, w in weights_hist.items()}).T.fillna(0.0)
wf_w = wf_w.reindex(columns=POOL).fillna(0.0)
wf_daily = pd.DataFrame(0.0, index=idx, columns=POOL)
for t, w in weights_hist.items():
    nxt = idx[idx.get_loc(t):]
    for s, ww in w.items():
        wf_daily.loc[nxt, s] = ww


def portfolio(wdf):
    """wdf: 日频目标权重(月内恒定)。T-1权重×T-1仓位×T日收益, 换手计费。"""
    rets = pd.DataFrame({s: ret_d[s] for s in POOL}).reindex(idx).fillna(0.0)
    poss = pd.DataFrame({s: pos_d[s].shift(1).fillna(0) for s in POOL}).reindex(idx).fillna(0.0)
    wlag = wdf.shift(1).fillna(0.0)
    gross = (wlag * poss * rets).sum(axis=1)
    turnover = (wlag * poss).diff().abs().sum(axis=1).fillna(0.0)
    net = gross - turnover * COST
    eq = 100.0 * (1 + net).cumprod()
    return eq


def static_w(spec):
    w = pd.DataFrame(0.0, index=idx, columns=POOL)
    for s, v in spec.items():
        w[s] = v
    return w


planA_scores = {s: float(np.exp(log_eq[s].iloc[-1])) / 100.0 / abs(float((np.exp(log_eq[s]) / np.exp(log_eq[s]).cummax() - 1).min())) for s in POOL}
tot = sum(planA_scores.values())
planA_spec = {s: v / tot for s, v in planA_scores.items()}
planB_spec = {"MU": 0.402, "KLAC": 0.228, "STX": 0.178, "TSM": 0.125, "CAT": 0.067}
res = {}
for label, wdf in [("C_walkforward贪心", wf_daily), ("A_静态全历史评分", static_w(planA_spec)), ("B_静态方案B", static_w(planB_spec))]:
    eq = portfolio(wdf)
    mdd = float((eq / eq.cummax() - 1).min())
    expo = float(wdf.shift(1).fillna(0).max(axis=1).mean())
    res[label] = (float(eq.iloc[-1]), mdd, expo)
    print(f"{label}: 终值{float(eq.iloc[-1]):.1f} MDD{mdd:.0%} 平均暴露{expo:.0%} 比值{float(eq.iloc[-1])/abs(mdd):.0f}")
pd.DataFrame([{ "plan": k, "final": v[0], "mdd": v[1], "exposure": v[2]} for k, v in res.items()]).to_csv(os.path.join(config.DATA_DIR, "allocator_walkforward_v1.csv"), index=False)
turn = (wf_daily.diff().abs().sum(axis=1) > 0).sum()
print(f"walk-forward决策月数: {len(weights_hist)}, 调仓次数: {turn}, 现金月占比: {1 - len([w for w in weights_hist.values() if w]) / max(len(weights_hist), 1):.0%}")
