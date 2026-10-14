# -*- coding: utf-8 -*-
"""审查三十轮: P0-2谷底四态归属(新入场/持仓穿越/持仓后退出/错过, 含谷内MAE) + P1-4真实独立策略净值账本edge评分
(T+1+15bp, 每标的独立账本)重跑四候选矩阵 + 同日历月去极值配对 + 循环块bootstrap(12月块, 500次)。"""
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
import mu_af_v2 as M

D = os.path.join(config.DATA_DIR, "mu")
COST = 0.0015
HOLD, DEDUP = 120, 60
TICKERS = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]
closes, dp_ev, v4_ev = {}, {}, {}
for sym in TICKERS:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    dp_ev[sym] = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    closes[sym] = c
v4tr = (M.v4.reindex(closes["MU"].index).fillna(0) > 0) & (M.v4.reindex(closes["MU"].index).fillna(0).diff() > 0)
v4_ev["MU"] = v4tr


def pos_unified(sym):
    c = closes[sym]
    raw = dp_ev[sym].values.copy()
    if sym in v4_ev:
        raw = raw | v4_ev[sym].values
    n = len(c)
    pos = np.zeros(n)
    in_pos = False
    entry_i = -999
    last = -9999
    for i in range(n):
        if raw[i] and i - last >= DEDUP:
            last = i
        if not in_pos and raw[i] and i == last:
            in_pos, entry_i = True, i
        elif in_pos and i - entry_i >= HOLD:
            in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=c.index)


def ledger_nav(c, pos):
    """真实独立策略净值账本: T+1, 15bp双边, 现金+份额。"""
    cash, shares = 100.0, 0.0
    nav = pd.Series(np.nan, index=c.index)
    pxs = c.values
    ps = pos.reindex(c.index).fillna(0.0).values
    in_pos = False
    for i in range(1, len(c)):
        p = pxs[i]
        want = bool(ps[i - 1] >= 0.5)
        if want and not in_pos:
            shares = cash * (1 - COST) / p
            cash = 0.0
            in_pos = True
        elif (not want) and in_pos:
            cash = shares * p * (1 - COST)
            shares = 0.0
            in_pos = False
        nav.iloc[i] = cash + shares * p
    return nav.ffill().fillna(100.0)


POSO = {s: pos_unified(s) for s in TICKERS}
NAV = {s: ledger_nav(closes[s], POSO[s]) for s in TICKERS}
idx = pd.concat(closes.values()).index.unique().sort_values()
POSO = pd.DataFrame({s: POSO[s].reindex(idx) for s in TICKERS}).fillna(0.0)
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in TICKERS}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx).notna() for s in TICKERS})
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index
SNR = pd.DataFrame({s: NAV[s].reindex(idx).resample("ME").last().pct_change() for s in TICKERS})
STRAT_MRET_REAL = SNR.reindex(months).fillna(0.0) if False else SNR
STRAT_MRET_PROXY = pd.DataFrame({s: POSO[s].shift(1).fillna(0) * PX[s].pct_change().fillna(0) for s in TICKERS}).resample("ME").apply(lambda x: (1 + x).prod() - 1)

print("=== P0-2 MU九谷四态归属(账户=统一R3机器+真实账本) ===")
c = closes["MU"]
pos = POSO["MU"].dropna()
ps = pos.values
pivots = []
hi = lo = c.values[0]
trend = 0
lo_i = 0
for i in range(1, n := len(c)):
    x = c.values[i]
    if trend >= 0:
        if x > hi:
            hi = x
        elif x <= hi * 0.70:
            pivots.append(("H", i))
            trend, lo, lo_i = -1, x, i
    else:
        if x < lo:
            lo, lo_i = x, i
        elif x >= lo * 1.30:
            pivots.append(("L", lo_i))
            trend, hi = 1, x
if trend < 0:
    pivots.append(("L", lo_i))
lows = sorted(set(i for k, i in pivots if k == "L"))
qual = [t for t in lows if n - (t + 1) >= 60 and float(c.values[t + 1:min(n, t + 121)].max() / c.values[t] - 1) >= 0.50]
for t in qual:
    pre = bool(ps[max(0, t - 1)] >= 0.5)  # 谷底前一日是否持仓
    post = next((i for i in range(t, min(n, t + 121)) if ps[i] >= 0.5 and (i == 0 or ps[i - 1] < 0.5)), None)  # 谷后新入场
    tdate = c.index[t]
    if pre:
        # 找该持仓的入场日
        e = t - 1
        while e > 0 and ps[e - 1] >= 0.5:
            e -= 1
        cost_px = c.values[e]
        trough_win = c.values[e:min(n, e + HOLD)]
        mae = float(min(trough_win) / cost_px - 1)
        exit_i = next((i2 for i2 in range(t, min(n, t + HOLD + 1)) if ps[i2] < 0.5), None)
        end_i = exit_i if exit_i else n - 1
        ret_hold = float(c.values[end_i] / cost_px - 1)
        print(f"谷{tdate.date()}: 持仓穿越(入场{c.index[e].date()}, 谷内MAE{mae:.0%}, 持至{c.index[end_i].date()}收{ret_hold:+.0%})")
    elif post is not None:
        print(f"谷{tdate.date()}: 新入场({c.index[post].date()}, 谷后{post - t}交易日)")
    else:
        print(f"谷{tdate.date()}: 完全错过(谷前无持仓, 谷后120日无新入场)")

print("\n=== P1-4 真实账本edge评分 vs 代理评分 — 四候选矩阵重跑 ===")


def score(sym, mi, win, kind, smr):
    if kind == "price":
        w = MCLOSE[sym].iloc[max(0, mi - win):mi].dropna()
        if len(w) < win or w.iloc[0] <= 0:
            return np.nan
        eqw = w / w.iloc[0]
    else:
        w = smr[sym].iloc[max(0, mi - win):mi]
        if len(w) < win:
            return np.nan
        eqw = (1 + w).cumprod()
    mdd = float((eqw / eqw.cummax() - 1).min())
    return float(eqw.iloc[-1]) / abs(mdd) if mdd else np.nan


def build_schedule(mi0, mi1, win, kind, smr):
    sched = {}
    for mi in range(mi0, mi1):
        m = months[mi]
        t_day = idx[idx.to_period("M") == m.to_period("M")][0]
        scores, act = {}, {}
        for s in TICKERS:
            sc = score(s, mi, win, kind, smr)
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


def run_pool(sched, day_slice, budget=100.0):
    cash = float(budget)
    shares = {s: 0.0 for s in TICKERS}
    cur_w = {s: 0.0 for s in TICKERS}
    exd = set(sched)
    eq = pd.Series(np.nan, index=day_slice)
    s_i = idx.get_loc(day_slice[0])
    for k, d in enumerate(day_slice):
        i = s_i + k
        sd = idx[i - 1] if i > 0 else d
        if d in exd:
            cur_w = dict(sched[d])
        px = PX.loc[d]
        for s in TICKERS:
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * POSO[s].get(sd, 0.0) * (cash + sum(shares[u] * px[u] for u in TICKERS if shares[u] > 0 and np.isfinite(px[u])))
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt < cur_v:
                dv = tgt - cur_v
                cash -= dv + abs(dv) * COST
                shares[s] += dv / px[s]
        pv = cash + sum(shares[s] * px[s] for s in TICKERS if shares[s] > 0 and np.isfinite(px[s]))
        buys = {}
        for s in TICKERS:
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
        pv = cash + sum(shares[s] * px[s] for s in TICKERS if shares[s] > 0 and np.isfinite(px[s]))
        assert cash >= -1e-8 and pv > 0
        eq.loc[d] = pv
    return eq


MI0 = 72
sd0 = idx[idx.to_period("M") == months[MI0].to_period("M")][0]
days_all = idx[idx.get_loc(sd0):]
for tag, smr in [("代理评分(POS×日收益)", STRAT_MRET_PROXY), ("真实账本评分(T+1+15bp)", STRAT_MRET_REAL)]:
    line = [tag]
    for label, (win, kind) in {"12m/edge": (12, "edge"), "36m/edge": (36, "edge"), "36m/price": (36, "price"), "60m/price": (60, "price")}.items():
        if kind == "price" and tag.startswith("真实"):
            continue
        sched = build_schedule(MI0, len(months), win, kind, smr)
        eq = run_pool(sched, days_all)
        r = eq.pct_change().dropna()
        blocks = []
        for b0, b1 in [("2015-01-01", "2020-01-01"), ("2020-01-01", "2025-01-01"), ("2025-01-01", None)]:
            seg = eq.loc[b0:b1] if b1 else eq.loc[b0:]
            if b1:
                seg = seg[seg.index < pd.Timestamp(b1)]
            blocks.append(f"{float(seg.iloc[-1]/seg.iloc[0]-1):+.0%}")
        line.append(f"{label}:{float(eq.iloc[-1]):.0f}({'+'.join(blocks)})")
    print(" | ".join(line))

print("\n=== 同日历月去Top3配对(36m/edge vs 36m/price, 公共起点2015-01) + 循环块bootstrap ===")
sch_e = build_schedule(MI0, len(months), 36, "edge", STRAT_MRET_REAL)
sch_p = build_schedule(MI0, len(months), 36, "price", STRAT_MRET_PROXY)
eq_e, eq_p = run_pool(sch_e, days_all), run_pool(sch_p, days_all)
me, mp = eq_e.resample("ME").last().pct_change().dropna(), eq_p.resample("ME").last().pct_change().dropna()
common = me.index.intersection(mp.index)
me, mp = me[common], mp[common]
diff = me - mp
for tag, s in [("edge", me), ("price", mp)]:
    top = s.nlargest(3).index
    print(f"{tag}: 全期{np.expm1(np.log1p(s).sum()):+.0%} 去同组Top3月{np.expm1(np.log1p(s.drop(top)).sum()):+.0%} (剔除{[str(t)[:7] for t in top]})")
rng = np.random.default_rng(7)
L = 12
boots = []
vals = diff.values
for _ in range(500):
    starts = rng.integers(0, len(vals), len(vals) // L + 1)
    sample = np.concatenate([vals[(s0 + np.arange(L)) % len(vals)] for s0 in starts])[:len(vals)]
    boots.append(np.expm1(np.log1p(pd.Series(sample)).sum()) - np.expm1(np.log1p(pd.Series(vals)).sum()) + np.expm1(np.log1p(vals).sum()) * 0)
bs = np.array(boots)
def boot_cum():
    out = []
    for _ in range(500):
        starts = rng.integers(0, len(vals), len(vals) // L + 1)
        sample = np.concatenate([vals[(s0 + np.arange(L)) % len(vals)] for s0 in starts])[:len(vals)]
        out.append(np.expm1(np.log1p(pd.Series(sample)).sum()))
    return np.array(out)

gap = np.expm1(np.log1p(me).sum()) - np.expm1(np.log1p(mp).sum())
bs = boot_cum()
lo, hi = np.percentile(bs, 5), np.percentile(bs, 95)
print(f"edge−price 月差全期累计: {gap:+.0%}; 循环块bootstrap(12月块500次)edge累计CI[5%,95%]: [{lo:+.0%}, {hi:+.0%}]; price基准累计{np.expm1(np.log1p(mp).sum()):+.0%}")
