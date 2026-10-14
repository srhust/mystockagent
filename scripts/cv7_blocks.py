# -*- coding: utf-8 -*-
"""审查二十四轮: ①P1公司行为扫描——复权后连续性核查(标记|日收益|>30%待人工核对日) ②P1不重叠时间块OOS——
冻结三候选(36m/orig, 60m/orig, 36m/edge)单一连续回测, 按块分段: 2015-2019/2020-2024/2025-2026。"""
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
print("=== 公司行为连续性扫描(复权后|日收益|>30% = 待人工核对) ===")
closes, raw_ev = {}, {}
for sym in TICKERS:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    r = c.pct_change().dropna()
    flags = r[abs(r) > 0.30]
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    raw_ev[sym] = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    closes[sym] = c
    print(f"{sym}: {'连续性OK(无>30%日)' if len(flags) == 0 else '待核对: ' + ', '.join(f'{d.date()}:{v:+.0%}' for d, v in flags.items())}")
POOL = list(closes)
idx = pd.concat(closes.values()).index.unique().sort_values()
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in POOL}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx).notna() for s in POOL})
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index


def pos_frozen(sym, mode):
    c = closes[sym]
    ev = raw_ev[sym].values
    n = len(c)
    evd, last = set(), -999
    for i in range(n):
        if ev[i] and i - last >= 120:
            evd.add(i)
            last = i
    ma100 = c.rolling(100).mean().values
    pxs = c.values
    pos = np.zeros(n)
    in_pos = False
    entry_i = peak = np.nan
    for i in range(n):
        if not in_pos and i in evd:
            in_pos, entry_i, peak = True, i, pxs[i]
        elif in_pos:
            peak = max(peak, pxs[i])
            dead = (i - entry_i >= 120) if mode == "orig" else ((pxs[i] < ma100[i]) or (pxs[i] < peak * 0.75)) if mode == "H" else (pxs[i] < ma100[i] if mode == "X2" else pxs[i] < peak * 0.75)
            if dead:
                in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=c.index)


POSV = {m: pd.DataFrame({s: pos_frozen(s, m).reindex(idx) for s in POOL}).fillna(0.0) for m in ("orig",)}
STRAT_MRET = pd.DataFrame({s: POSV["orig"][s].shift(1).fillna(0) * PX[s].pct_change().fillna(0) for s in POOL}).resample("ME").apply(lambda x: (1 + x).prod() - 1)


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
    P = POSV["orig"]
    sched = {}
    for mi in range(mi0, mi1):
        m = months[mi]
        t_day = idx[idx.to_period("M") == m.to_period("M")][0]
        scores, act = {}, {}
        for s in POOL:
            sc = score(s, mi, win, kind)
            if np.isfinite(sc):
                scores[s] = sc
                act[s] = bool(P[s].reindex(idx[:idx.get_loc(t_day) + 1]).iloc[-1] >= 0.5)
        elig = {s: sc for s, sc in scores.items() if act.get(s)}
        w = {}
        if elig:
            rank = sorted(elig, key=elig.get, reverse=True)
            w = {rank[0]: 0.6}
            if len(rank) > 1:
                w[rank[1]] = 0.4
        exec_day = idx[idx.get_loc(t_day) + 1] if idx.get_loc(t_day) + 1 < len(idx) else t_day
        sched[exec_day] = w
    return sched


def run_ledger(weight_schedule, day_slice, budget=100.0):
    cash = float(budget)
    shares = {s: 0.0 for s in POOL}
    cur_w = {s: 0.0 for s in POOL}
    exec_days = set(weight_schedule)
    eq = pd.Series(np.nan, index=day_slice)
    expo = pd.Series(np.nan, index=day_slice)
    start_i = idx.get_loc(day_slice[0])
    for k, d in enumerate(day_slice):
        i = start_i + k
        sd = idx[i - 1] if i > 0 else d
        if d in exec_days:
            cur_w = dict(weight_schedule[d])
        px = PX.loc[d]
        for s in POOL:
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * POSV["orig"][s].get(sd, 0.0) * (cash + sum(shares[u] * px[u] for u in POOL if shares[u] > 0 and np.isfinite(px[u])))
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt < cur_v:
                dv = tgt - cur_v
                cash -= dv + abs(dv) * COST
                shares[s] += dv / px[s]
        mv0 = sum(shares[s] * px[s] for s in POOL if shares[s] > 0 and np.isfinite(px[s]))
        port_val = cash + mv0
        buys = {}
        for s in POOL:
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * POSV["orig"][s].get(sd, 0.0) * port_val
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt > cur_v:
                buys[s] = tgt - cur_v
        need = sum(buys.values())
        scale = min(1.0, max(cash, 0.0) / (1 + COST) / need) if need > 0 else 0.0
        for s, dv in buys.items():
            dv *= scale
            if abs(dv) > port_val * 1e-6:
                cash -= dv + dv * COST
                shares[s] += dv / px[s]
        mv = sum(shares[s] * px[s] for s in POOL if shares[s] > 0 and np.isfinite(px[s]))
        port_val = cash + mv
        assert cash >= -1e-8 and np.isfinite(port_val) and port_val > 0
        eq.loc[d] = port_val
        expo.loc[d] = mv / port_val
    return eq, expo


MI0 = 72
sd0 = idx[idx.to_period("M") == months[MI0].to_period("M")][0]
days_all = idx[idx.get_loc(sd0):]
print(f"\n=== 不重叠时间块OOS(单一连续回测分段, 起点{days_all[0].date()}) ===")
CANDS = {"36m/orig": (36, "price"), "60m/orig": (60, "price"), "36m/edge": (36, "edge")}
BLOCKS = [("2015-2019", "2015-01", "2020-01"), ("2020-2024", "2020-01", "2025-01"), ("2025-2026", "2025-01", None)]
for label, (win, kind) in CANDS.items():
    sched = build_schedule(MI0, len(months), win, kind)
    eq, expo = run_ledger(sched, days_all)
    out = [label]
    for bname, b0, b1 in BLOCKS:
        seg = eq.loc[b0:b1] if b1 else eq.loc[b0:]
        r = seg.pct_change().dropna()
        mdd = float((seg / seg.cummax() - 1).min())
        ret = float(seg.iloc[-1] / seg.iloc[0] - 1)
        shp = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan
        out.append(f"{bname}: {ret:+.0%}/MDD{mdd:.0%}/Sh{shp:.2f}")
    print(" | ".join(out))
print("注: 三块为同一起点的连续账户分段, 块内含既定策略持仓惯性; 三块时间不重叠, 但样本仍受少量独立周期限制")
