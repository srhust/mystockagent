# -*- coding: utf-8 -*-
"""审查二十七轮: ①R3再入场机制(60日去重)接入池级分配器 — 三候选时间块重跑(36m/orig, 60m/orig, 36m/edge)
②MU专项归因: 2010-08遗漏谷(唯一未覆盖)的信号成分诊断 + 2020-03闪电谷的入场速度与V型捕获。"""
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
COST = 0.0015
TICKERS = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]
closes, raw_ev = {}, {}
for sym in TICKERS:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    raw_ev[sym] = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    closes[sym] = c
POOL = list(closes)
idx = pd.concat(closes.values()).index.unique().sort_values()
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in POOL}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx).notna() for s in POOL})
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index


def pos_r3(sym, dedup=60, hold=120):
    """cv9参考实现: 原始信号60日去重入场, 120日限时退出, 退出后仅新事件再入。"""
    c = closes[sym]
    ev = raw_ev[sym].values
    n = len(c)
    pos = np.zeros(n)
    in_pos = False
    entry_i = -999
    last_ev = -9999
    for i in range(n):
        if ev[i] and i - last_ev >= dedup:
            last_ev = i
        if not in_pos and last_ev >= 0 and i >= last_ev and (i - last_ev) <= 5:
            # 入场: 事件后5日内(事件日为决策, T+1成交由账本执行)
            in_pos, entry_i = True, i
        elif in_pos and i - entry_i >= hold:
            in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=c.index)


POSO = pd.DataFrame({s: pos_r3(s).reindex(idx) for s in POOL}).fillna(0.0)
STRAT_MRET = pd.DataFrame({s: POSO[s].shift(1).fillna(0) * PX[s].pct_change().fillna(0) for s in POOL}).resample("ME").apply(lambda x: (1 + x).prod() - 1)


def score(sym, mi, win, kind):
    if kind == "price":
        w = MCLOSE[sym].iloc[max(0, mi - win):mi].dropna()
        if len(w) < win or w.iloc[0] <= 0:
            return np.nan
        eqw = w / w.iloc[0]
    else:
        w = STRAT_MRET[sym].iloc[max(0, mi - win):mi]
        if len(w) < win:
            return np.nan
        eqw = (1 + w).cumprod()
    mdd = float((eqw / eqw.cummax() - 1).min())
    return float(eqw.iloc[-1]) / abs(mdd) if mdd else np.nan


def build_schedule(mi0, mi1, win, kind):
    sched = {}
    for mi in range(mi0, mi1):
        m = months[mi]
        t_day = idx[idx.to_period("M") == m.to_period("M")][0]
        scores, act = {}, {}
        for s in POOL:
            sc = score(s, mi, win, kind)
            if np.isfinite(sc):
                scores[s] = sc
                act[s] = bool(POSO[s].reindex(idx[:idx.get_loc(t_day) + 1]).iloc[-1] >= 0.5)
        elig = {s: v for s, v in scores.items() if act.get(s)}
        w = {}
        if elig:
            rank = sorted(elig, key=elig.get, reverse=True)
            w = {rank[0]: 0.6} | ({rank[1]: 0.4} if len(rank) > 1 else {})
        sched[idx[idx.get_loc(t_day) + 1]] = w
    return sched


def run_ledger(sched, day_slice, budget=100.0):
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
            tgt = cur_w.get(s, 0.0) * POSO[s].get(sd, 0.0) * (cash + sum(shares[u] * px[u] for u in POOL if shares[u] > 0 and np.isfinite(px[u])))
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt < cur_v:
                dv = tgt - cur_v
                cash -= dv + abs(dv) * COST
                shares[s] += dv / px[s]
        mv0 = sum(shares[s] * px[s] for s in POOL if shares[s] > 0 and np.isfinite(px[s]))
        pv = cash + mv0
        buys = {}
        for s in POOL:
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * POSO[s].get(sd, 0.0) * pv
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
days_all = idx[idx.get_loc(sd0):]
print(f"=== R3(60日去重)接入池级分配器, 公共起点{days_all[0].date()} ===")
CANDS = {"36m/orig": (36, "price"), "60m/orig": (60, "price"), "36m/edge": (36, "edge")}
for label, (win, kind) in CANDS.items():
    sched = build_schedule(MI0, len(months), win, kind)
    eq, expo = run_ledger(sched, days_all)
    out = [label]
    for bname, b0, b1 in [("2015-2019", "2015-01", "2020-01"), ("2020-2024", "2020-01", "2025-01"), ("2025-2026", "2025-01", None)]:
        seg = eq.loc[b0:b1] if b1 else eq.loc[b0:]
        r = seg.pct_change().dropna()
        out.append(f"{bname}: {float(seg.iloc[-1]/seg.iloc[0]-1):+.0%}/MDD{float((seg/seg.cummax()-1).min()):.0%}/Sh{float(r.mean()/r.std()*np.sqrt(252)) if r.std()>0 else np.nan:.2f}")
    print(" | ".join(out))

print("\n=== MU 2010-08遗漏谷归因(信号成分诊断) ===")
c = closes["MU"]
f = build_features(c, [])
dd = f["dd252"]
ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool)
t0 = c.index.get_loc(pd.Timestamp("2010-08-31"))
win = c.iloc[t0:t0 + 130]
comp = pd.DataFrame({
    "dd252": dd.iloc[t0:t0 + 130], "state250": state250.iloc[t0:t0 + 130],
    "A_MA20收复": (c > ma20).iloc[t0:t0 + 130], "A_MA20上行": (ma20 > ma20.shift(5)).iloc[t0:t0 + 130],
    "A_dd≤-40": (dd <= -0.40).iloc[t0:t0 + 130], "A_近MA50": (c <= ma50 * 1.05).iloc[t0:t0 + 130],
}).iloc[::15]
print(comp.round(2).to_string())
print("B路径(MA50上拐+回撤10-30%):")
b = ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)).iloc[t0:t0 + 130]
print(f"  130日内B路径为真天数: {int(b.sum())}")

print("\n=== MU 2020-03闪电谷入场速度 ===")
tq = c.index.get_loc(pd.Timestamp("2020-03-16"))
ev = raw_ev["MU"].values
nxt = next((i for i in range(tq, tq + 130) if ev[i]), None)
if nxt:
    efill = nxt + 1
    top = c.iloc[tq:c.index.get_loc(pd.Timestamp("2021-06-01"))].max()
    print(f"谷{c.index[tq].date()} → 首信号{c.index[nxt].date()}(滞后{nxt-tq}交易日) → T+1成交{c.index[efill].date()} @ {c.iloc[efill]:.2f}")
    print(f"谷价{c.iloc[tq]:.2f} → V顶{top:.2f}(+{top/c.iloc[tq]-1:.0%}) | 成交价较谷高{c.iloc[efill]/c.iloc[tq]-1:.0%} → 谷到顶捕获空间剩余{(top/c.iloc[efill]-1)/(top/c.iloc[tq]-1):.0%}")
