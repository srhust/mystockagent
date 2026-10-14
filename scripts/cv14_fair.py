# -*- coding: utf-8 -*-
"""审查三十一轮: P0修复参与度评价(周期窗口内+实际成交FIFO实现收益) + P0零回撤分母诊断(12m vs 36m有效候选率/事件后生效时滞)
+ P1 12m/edge vs 36m/edge配对(同月去极值+循环块bootstrap, 统一真实账本评分)。"""
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
    e_i = -999
    last = -9999
    for i in range(n):
        if raw[i] and i - last >= DEDUP:
            last = i
        if not in_pos and raw[i] and i == last:
            in_pos, e_i = True, i
        elif in_pos and i - e_i >= HOLD:
            in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=c.index)


def ledger_nav(c, pos):
    cash, shares = 100.0, 0.0
    nav = pd.Series(np.nan, index=c.index)
    pxs = c.values
    ps = pos.reindex(c.index).fillna(0.0).values
    in_pos = False
    for i in range(1, len(c)):
        p = pxs[i]
        want = bool(ps[i - 1] >= 0.5)
        if want and not in_pos:
            shares, cash, in_pos = cash * (1 - COST) / p, 0.0, True
        elif (not want) and in_pos:
            cash, shares, in_pos = shares * p * (1 - COST), 0.0, False
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
SMR = pd.DataFrame({s: NAV[s].reindex(idx).resample("ME").last().pct_change() for s in TICKERS}).reindex(months).fillna(0.0)

# ===== P0: 零回撤分母诊断 =====
print("=== P0 零回撤分母诊断(真实账本edge, MI0=72起) ===")
for win in (12, 36):
    valid = zero_mdd = nohist = 0
    for mi in range(MI0 := 72, len(months)):
        for s in TICKERS:
            w = SMR[s].iloc[max(0, mi - win):mi]
            if len(w) < win:
                nohist += 1
                continue
            eqw = (1 + w).cumprod()
            mdd = float((eqw / eqw.cummax() - 1).min())
            if mdd == 0:
                zero_mdd += 1
            else:
                valid += 1
    tot = valid + zero_mdd + nohist
    print(f"{win}m/edge: 候选格{tot} 有效{valid}({valid/tot:.0%}) 零回撤排除{zero_mdd}({zero_mdd/tot:.0%}) 历史不足{nohist}({nohist/tot:.0%})")

c = closes["MU"]
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
mi_of_day = {m: mi for mi, m in enumerate(months)}
print("MU谷后评分首次有效时滞(月):")
for t in qual:
    tdate = c.index[t]
    tmi = months.searchsorted(pd.Timestamp(tdate) + pd.offsets.MonthEnd(0))
    lag12 = lag36 = None
    for mi in range(tmi, min(len(months), tmi + 24)):
        for win, lst in ((12, "l12"), (36, "l36")):
            w = SMR["MU"].iloc[max(0, mi - win):mi]
            if len(w) < win:
                continue
            eqw = (1 + w).cumprod()
            mdd = float((eqw / eqw.cummax() - 1).min())
            if mdd != 0:
                if win == 12 and lag12 is None:
                    lag12 = mi - tmi
                if win == 36 and lag36 is None:
                    lag36 = mi - tmi
        if lag12 is not None and lag36 is not None:
            break
    print(f"  谷{tdate.date()}: 12m有效滞后{lag12}月 36m有效滞后{lag36}月")

# ===== P0修复: 周期窗口内实际成交参与度(FIFO实现收益) =====
print("\n=== P0修复 参与度: MU周期窗口(谷→下一谷, 上限240日)内实际买入FIFO实现收益 ===")


def score(sym, mi, win, kind):
    if kind == "price":
        w = MCLOSE[sym].iloc[max(0, mi - win):mi].dropna()
        if len(w) < win or w.iloc[0] <= 0:
            return np.nan
        eqw = w / w.iloc[0]
    else:
        w = SMR[sym].iloc[max(0, mi - win):mi]
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
        for s in TICKERS:
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


def run_pool_log(sched, day_slice, budget=100.0):
    cash = float(budget)
    shares = {s: 0.0 for s in TICKERS}
    cur_w = {s: 0.0 for s in TICKERS}
    exd = set(sched)
    eq = pd.Series(np.nan, index=day_slice)
    fills = []
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
                fills.append((d, s, dv / px[s], px[s], "S"))
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
                fills.append((d, s, dv / px[s], px[s], "B"))
                shares[s] += dv / px[s]
        pv = cash + sum(shares[s] * px[s] for s in TICKERS if shares[s] > 0 and np.isfinite(px[s]))
        assert cash >= -1e-8 and pv > 0
        eq.loc[d] = pv
    return eq, fills


MI0 = 72
sd0 = idx[idx.to_period("M") == months[MI0].to_period("M")][0]
days_all = idx[idx.get_loc(sd0):]
tr_windows = []
for j, t in enumerate(qual):
    t0 = qual[j + 1] if j + 1 < len(qual) else min(n, t + 240)
    tr_windows.append((t, min(t0, t + 240)))
CANDS = {"12m/edge": (12, "edge"), "36m/edge": (36, "edge"), "36m/price": (36, "price"), "60m/price": (60, "price")}
run_res = {}
for label, (win, kind) in CANDS.items():
    sched = build_schedule(MI0, len(months), win, kind)
    eq, fills = run_pool_log(sched, days_all)
    run_res[label] = (eq, fills)
    r = eq.pct_change().dropna()
    print(f"{label}: 终值{float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sharpe{float(r.mean()/r.std()*np.sqrt(252)):.2f}")

hdr = "周期窗 | " + " | ".join(CANDS)
print("\n" + hdr)
for (t, t1) in tr_windows:
    row = [f"{c.index[t].date()}→{c.index[min(t1, n-1)].date()}"]
    for label in CANDS:
        _, fills = run_res[label]
        mu_buys = [(d, sh, p) for d, s, sh, p, k in fills if s == "MU" and k == "B" and c.index.get_loc(d) >= t and c.index.get_loc(d) < t1]
        if not mu_buys:
            row.append("未买MU")
            continue
        d0, _, _ = mu_buys[0]
        lag = c.index.get_loc(d0) - t
        el = float(c.loc[d0] / c.iloc[t] - 1)
        # FIFO实现收益: 窗口内买的MU股, 在窗口后+120日内卖出部分
        lots = [(sh, p) for _, sh, p in mu_buys]
        pnl = 0.0
        cost_tot = sum(sh * p for sh, p in lots)
        sells = [(d, abs(sh), p) for d, s, sh, p, k in fills if s == "MU" and k == "S" and c.index.get_loc(d) >= t]
        for d2, sh, p in sells:
            rem = sh
            while rem > 1e-9 and lots:
                sh0, p0 = lots[0]
                take = min(sh0, rem)
                pnl += take * p * (1 - COST) - take * p0 * (1 + COST)
                if sh0 <= rem + 1e-12:
                    lots.pop(0)
                else:
                    lots[0] = (sh0 - rem, p0)
                rem -= take
        row.append(f"滞后{lag}日/已涨{el:.0%}/实现{pnl:.1f}(本{cost_tot:.1f})")
    print(" | ".join(row))

# ===== P1: 12m/edge vs 36m/edge 配对 =====
print("\n=== P1 12m/edge vs 36m/edge 配对(同月去极值+循环块bootstrap) ===")
me, mp = run_res["12m/edge"][0], run_res["36m/edge"][0]
rme, rmp = me.resample("ME").last().pct_change().dropna(), mp.resample("ME").last().pct_change().dropna()
common = rme.index.intersection(rmp.index)
rme, rmp = rme[common], rmp[common]
tops = set(rme.nlargest(3).index) | set(rmp.nlargest(3).index)
for tag, s in [("12m/edge", rme), ("36m/edge", rmp)]:
    print(f"{tag}: 全期{np.expm1(np.log1p(s).sum()):+.0%} 去共同Top3月{np.expm1(np.log1p(s.drop(list(tops))).sum()):+.0%}")
diff = (rme - rmp).values
L = 12
rng = np.random.default_rng(7)
bs = []
for _ in range(500):
    starts = rng.integers(0, len(diff), len(diff) // L + 1)
    sample = np.concatenate([diff[(s0 + np.arange(L)) % len(diff)] for s0 in starts])[:len(diff)]
    bs.append(float(np.prod(1 + sample) - 1))
lo, hi2 = np.percentile(bs, 5), np.percentile(bs, 95)
print(f"12m−36m 循环块bootstrap(12月块500次)累计差CI[5%,95%]: [{lo:+.0%}, {hi2:+.0%}] (点估计{np.prod(1+diff)-1:+.0%})")
