# -*- coding: utf-8 -*-
"""审查二十五轮: ①P2入场机制增量 — MU单标的同账本(冻结orig限时退出): 仅V4入场 vs 仅双路径 vs V4∪双路径,
统一120日事件去重+120日限时退出, 指标=事件数/九谷覆盖/逐谷净收益/账户终值MDD ②H反向对照 — orig目标仓位
按暴露比缩至H均值暴露(无杠杆), 比较MDD/Sharpe判断H是否有额外避险能力。"""
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
from p2_exit_rerun import run_position_ledger
import mu_af_v2 as M

c = M.c
f = build_features(c, [])
dd = f["dd252"]
ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
dp = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
v4 = (M.v4.reindex(c.index).fillna(0) > 0) & (M.v4.reindex(c.index).fillna(0).diff() > 0)  # v4入场日=0→1
v4e = pd.Series(v4.values, index=c.index).rolling(120, min_periods=1).max().astype(float)
dpe = pd.Series(dp.values, index=c.index).rolling(120, min_periods=1).max().astype(float)
union = pd.Series((v4.values | dp.values), index=c.index).rolling(120, min_periods=1).max().astype(float)
n = len(c)
pxs = c.values


def pos_from_events(ev_series, mode="orig"):
    evd = set(np.where(ev_series.diff().fillna(0) > 0)[0])
    pos = np.zeros(n)
    in_pos = False
    entry_i = -999
    for i in range(n):
        if not in_pos and i in evd:
            in_pos, entry_i = True, i
        elif in_pos and i - entry_i >= 120:
            in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=c.index), evd


# 九个合格谷(沿用审计口径)
hi_i = lo_i = 0
hi = lo = pxs[0]
trend, pivots = 0, []
for i in range(1, n):
    x = pxs[i]
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
lows = sorted(set(i for k, i in pivots if k == "L"))
qual = [t for t in lows if n - (t + 1) >= 60 and float(pxs[t + 1:min(n, t + 121)].max() / pxs[t] - 1) >= 0.50]

print(f"=== 入场机制对比(MU, 冻结orig限时退出, 事件120日去重) ===")
for label, es in [("仅V4", v4e), ("仅双路径", dpe), ("V4∪双路径", union)]:
    pos, evd = pos_from_events(es)
    eq, led = run_position_ledger(c, pos)
    ev_list = sorted(evd)
    cov = sum(1 for t in qual if any(t <= e <= t + 120 for e in ev_list))
    L = pd.DataFrame(led, columns=["d", "ret", "hold"])
    pf = L[L.ret > 0].ret.sum() / abs(L[L.ret <= 0].ret.sum()) if len(L) and (L.ret <= 0).any() else np.inf
    per = []
    for t in qual:
        e = next((e for e in ev_list if t <= e <= t + 120), None)
        tr = next((r for d2, r, _ in led if c.index.get_loc(d2) >= t and c.index.get_loc(d2) <= t + 240), None) if e is not None else None
        per.append(f"{c.index[t].date().year}:{'无' if tr is None else f'{tr:+.0%}'}")
    print(f"{label}: 事件{len(evd)} 九谷覆盖{cov}/9 账户{float(eq.iloc[-1]):.1f}/MDD{float((eq/eq.cummax()-1).min()):.0%} 笔{len(L)} 胜率{(L.ret>0).mean():.0%} PF{pf:.2f}")
    print(f"   逐谷净收益: {' | '.join(per)}")

print("\n=== H反向对照(orig目标仓位缩至H平均暴露, 无杠杆, 冻结36m价格调度) ===")
COST = 0.0015
D = os.path.join(config.DATA_DIR, "mu")
closes, raw_ev2 = {}, {}
for sym in ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]:
    cc = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    ff = build_features(cc, [])
    ddx = ff["dd252"]
    m20, m50 = cc.rolling(20).mean(), cc.rolling(50).mean()
    st250 = (ddx <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    raw_ev2[sym] = (((cc > m20) & (m20 > m20.shift(5)) & st250 & (ddx <= -0.40) & (cc <= m50 * 1.05)) | ((m50 > m50.shift(10)) & (ddx >= -0.30) & (ddx <= -0.10)))
    closes[sym] = cc
POOL = list(closes)
idx = pd.concat(closes.values()).index.unique().sort_values()
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in POOL}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx).notna() for s in POOL})
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index


def pos_orig(sym):
    cc = closes[sym]
    ev = raw_ev2[sym].values
    nn = len(cc)
    evd, last = set(), -999
    for i in range(nn):
        if ev[i] and i - last >= 120:
            evd.add(i)
            last = i
    pxs2 = cc.values
    pos = np.zeros(nn)
    in_pos = False
    e_i = -999
    for i in range(nn):
        if not in_pos and i in evd:
            in_pos, e_i = True, i
        elif in_pos and i - e_i >= 120:
            in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=cc.index)


POSO = pd.DataFrame({s: pos_orig(s).reindex(idx) for s in POOL}).fillna(0.0)


def run_pool_ledger(sched, P, day_slice, budget=100.0):
    cash = float(budget)
    shares = {s: 0.0 for s in POOL}
    cur_w = {s: 0.0 for s in POOL}
    exd = set(sched)
    eq = pd.Series(np.nan, index=day_slice)
    expo = pd.Series(np.nan, index=day_slice)
    s_i = idx.get_loc(day_slice[0])
    for k, d in enumerate(day_slice):
        i = s_i + k
        sd = idx[i - 1] if i > 0 else d
        if d in exd:
            cur_w = dict(sched[d])
        px = PX.loc[d]
        for s in POOL:
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * P[s].get(sd, 0.0) * (cash + sum(shares[u] * px[u] for u in POOL if shares[u] > 0 and np.isfinite(px[u])))
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt < cur_v:
                dv = tgt - cur_v
                cash -= dv + abs(dv) * COST
                shares[s] += dv / px[s]
        mv0 = sum(shares[s] * px[s] for s in POOL if shares[s] > 0 and np.isfinite(px[s]))
        pv = cash + mv0
        buys = {}
        for s in POOL:
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * P[s].get(sd, 0.0) * pv
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt > cur_v:
                buys[s] = tgt - cur_v
        need = sum(buys.values())
        sc = min(1.0, max(cash, 0.0) / (1 + COST) / need) if need > 0 else 0.0
        for s, dv in buys.items():
            dv *= sc
            if abs(dv) > pv * 1e-6:
                cash -= dv + dv * COST
                shares[s] += dv / px[s]
        mv = sum(shares[s] * px[s] for s in POOL if shares[s] > 0 and np.isfinite(px[s]))
        pv = cash + mv
        assert cash >= -1e-8 and pv > 0
        eq.loc[d] = pv
        expo.loc[d] = mv / pv
    return eq, expo


MI0 = 72
sd0 = idx[idx.to_period("M") == months[MI0].to_period("M")][0]
days = idx[idx.get_loc(sd0):]
sched = {}
for mi in range(MI0, len(months)):
    m = months[mi]
    t_day = idx[idx.to_period("M") == m.to_period("M")][0]
    scores, act = {}, {}
    for s in POOL:
        w = MCLOSE[s].iloc[max(0, mi - 36):mi].dropna()
        if len(w) < 36 or w.iloc[0] <= 0:
            continue
        eqw = w / w.iloc[0]
        mdd = float((eqw / eqw.cummax() - 1).min())
        if not mdd:
            continue
        scores[s] = float(eqw.iloc[-1]) / abs(mdd)
        act[s] = bool(POSO[s].reindex(idx[:idx.get_loc(t_day) + 1]).iloc[-1] >= 0.5)
    elig = {s: v for s, v in scores.items() if act.get(s)}
    ww = {}
    if elig:
        rank = sorted(elig, key=elig.get, reverse=True)
        ww = {rank[0]: 0.6} | ({rank[1]: 0.4} if len(rank) > 1 else {})
    sched[idx[idx.get_loc(t_day) + 1]] = ww
eO, xO = run_pool_ledger(sched, POSO, days)
for tag, f_eff in [("orig全暴露", 1.0), ("orig×0.61(≈H暴露0.56)", 0.61)]:
    eq, expo = run_pool_ledger(sched, POSO * f_eff, days)
    r = eq.pct_change().dropna()
    print(f"{tag}: 终值{float(eq.iloc[-1]):.1f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sharpe{float(r.mean()/r.std()*np.sqrt(252)):.2f} 暴露{float(expo.mean()):.0%}")
print("(H原样: 终值555.6 MDD-43% Sharpe0.68 暴露44% — 来自cv5同口径, 供对照)")
