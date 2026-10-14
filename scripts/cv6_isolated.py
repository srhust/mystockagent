# -*- coding: utf-8 -*-
"""审查二十三轮: ①P0退出独立效应隔离——机会排名/入场候选冻结到参考持仓(orig), 各退出变体仅改变执行侧POS
②P1 H同暴露对照(H日收益按暴露比放大) ③P1 strategy_edge_score vs price_momentum同窗同执行正面对照 ④循环块自助。"""
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
closes, raw_ev = {}, {}
for sym in ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]:
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
            if mode == "orig":
                dead = i - entry_i >= 120
            elif mode == "X2":
                dead = pxs[i] < ma100[i]
            elif mode == "X3":
                dead = pxs[i] < peak * 0.75
            else:
                dead = (pxs[i] < ma100[i]) or (pxs[i] < peak * 0.75)
            if dead:
                in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=c.index)


POSV = {m: pd.DataFrame({s: pos_frozen(s, m).reindex(idx) for s in POOL}).fillna(0.0) for m in ("orig", "X2", "X3", "H")}
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


def build_schedule(mi0, mi1, win, kind, ref_pos):
    """审查二十三轮P0: 排名与入场候选一律用参考持仓(orig), 与执行侧退出变体解耦。"""
    sched = {}
    for mi in range(mi0, mi1):
        m = months[mi]
        t_day = idx[idx.to_period("M") == m.to_period("M")][0]
        scores, act = {}, {}
        for s in POOL:
            sc = score(s, mi, win, kind)
            if np.isfinite(sc):
                scores[s] = sc
                act[s] = bool(ref_pos[s].reindex(idx[:idx.get_loc(t_day) + 1]).iloc[-1] >= 0.5)
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


def run_ledger(weight_schedule, day_slice, P, budget=100.0):
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
            tgt = cur_w.get(s, 0.0) * P[s].get(sd, 0.0) * (cash + sum(shares[u] * px[u] for u in POOL if shares[u] > 0 and np.isfinite(px[u])))
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt < cur_v:
                dv = tgt - cur_v
                cash -= dv + abs(dv) * COST
                shares[s] += dv / px[s]
        mv0 = sum(shares[s] * px[s] for s in POOL if shares[s] > 0 and np.isfinite(px[s]))
        port_val = cash + mv0
        buys = {}
        for s in POOL:
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * P[s].get(sd, 0.0) * port_val
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


def stats(eq, expo):
    r = eq.pct_change().dropna()
    yrs = len(r) / 252
    return dict(final=float(eq.iloc[-1]), mdd=float((eq / eq.cummax() - 1).min()),
                cagr=float(eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1,
                sharpe=float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan,
                cash=float(1 - expo.mean()))


MI0 = 72
sd0 = idx[idx.to_period("M") == months[MI0].to_period("M")][0]
sd0 = idx[idx.get_loc(sd0):]
print(f"公共起点: {sd0[0].date()} | 排名/入场冻结到参考持仓(orig) — 退出变体仅改变执行侧")
print("\n=== 退出独立效应网格 ===")
rows = []
for win in (12, 36, 60):
    sched = build_schedule(MI0, len(months), win, "price", POSV["orig"])
    for mode in ("orig", "X2", "X3", "H"):
        eq, expo = run_ledger(sched, sd0, POSV[mode])
        st = stats(eq, expo)
        rows.append(dict(win=win, exit=mode, **st))
        print(f"{win}月/{mode}: 终值{st['final']:.1f} MDD{st['mdd']:.0%} CAGR{st['cagr']:.0%} Sharpe{st['sharpe']:.2f} 现金{st['cash']:.0%}")
pd.DataFrame(rows).to_csv(os.path.join(config.DATA_DIR, "cv6_isolated_grid.csv"), index=False)

print("\n=== H同暴露对照(放大至与orig同均值暴露) ===")
for win in (12, 36, 60):
    sched = build_schedule(MI0, len(months), win, "price", POSV["orig"])
    eO, xO = run_ledger(sched, sd0, POSV["orig"])
    eH, xH = run_ledger(sched, sd0, POSV["H"])
    lam = float(xO.mean() / xH.mean())
    rH = eH.pct_change().fillna(0) * lam
    eHl = 100 * (1 + rH).cumprod()
    sO, sH = stats(eO, xO), stats(eHl, xH)
    print(f"{win}月: orig 终值{sO['final']:.1f}/MDD{sO['mdd']:.0%}/Sharpe{sO['sharpe']:.2f} | H×{lam:.2f} 终值{sH['final']:.1f}/MDD{sH['mdd']:.0%}/Sharpe{sH['sharpe']:.2f}")

print("\n=== 评分类型对照(36月窗, 同调度同执行, 参考持仓orig) ===")
for kind in ("price", "edge"):
    sched = build_schedule(MI0, len(months), 36, kind, POSV["orig"])
    eq, expo = run_ledger(sched, sd0, POSV["orig"])
    st = stats(eq, expo)
    print(f"{kind}_momentum_score: 终值{st['final']:.1f} MDD{st['mdd']:.0%} CAGR{st['cagr']:.0%} Sharpe{st['sharpe']:.2f}")

schedP = build_schedule(MI0, len(months), 36, "price", POSV["orig"])
schedE = build_schedule(MI0, len(months), 36, "edge", POSV["orig"])
eP, _ = run_ledger(schedP, sd0, POSV["orig"])
eE, _ = run_ledger(schedE, sd0, POSV["orig"])
dP = eP.resample("ME").last().pct_change().dropna()
dE = eE.resample("ME").last().pct_change().dropna()
diff = (dE - dP).values
rng = np.random.default_rng(42)
L, nIt = 6, 2000
means = []
for _ in range(nIt):
    smp = []
    while len(smp) < len(diff):
        s = rng.integers(0, len(diff))
        smp.extend(diff[s:s + L])
    means.append(np.mean(smp[:len(diff)]))
lo, hi = np.percentile(means, [5, 95])
print(f"edge−price 月差 {diff.mean():+.3%} 循环块90%CI [{lo:+.3%}, {hi:+.3%}] {'显著' if lo > 0 or hi < 0 else '不显著(含0)'}")

