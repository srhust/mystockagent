# -*- coding: utf-8 -*-
"""审查二十九轮: 2×2矩阵(评分窗12/36 × 类型price/edge) — 统一R3机制(cv11同源), 同事件同入场同退出同账本。
输出: 全期+左闭右开三块+多起点(2015/2017/2019/2021固定5年) + MU九谷参与度(谷→首次分配滞后/已涨/配置期捕获)。"""
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


def position_state_machine(c, sym):
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


POSO = pd.DataFrame({s: position_state_machine(closes[s], s).reindex(pd.concat(closes.values()).index.unique().sort_values()) for s in TICKERS}).fillna(0.0)
idx = pd.concat(closes.values()).index.unique().sort_values()
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in TICKERS}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx).notna() for s in TICKERS})
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index
STRAT_MRET = pd.DataFrame({s: POSO[s].shift(1).fillna(0) * PX[s].pct_change().fillna(0) for s in TICKERS}).resample("ME").apply(lambda x: (1 + x).prod() - 1)


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


def run_pool(sched, day_slice, budget=100.0):
    cash = float(budget)
    shares = {s: 0.0 for s in TICKERS}
    cur_w = {s: 0.0 for s in TICKERS}
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
        for s in TICKERS:
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * POSO[s].get(sd, 0.0) * (cash + sum(shares[u] * px[u] for u in TICKERS if shares[u] > 0 and np.isfinite(px[u])))
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt < cur_v:
                dv = tgt - cur_v
                cash -= dv + abs(dv) * COST
                shares[s] += dv / px[s]
        mv0 = sum(shares[s] * px[s] for s in TICKERS if shares[s] > 0 and np.isfinite(px[s]))
        pv = cash + mv0
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
        mv = sum(shares[s] * px[s] for s in TICKERS if shares[s] > 0 and np.isfinite(px[s]))
        pv = cash + mv
        assert cash >= -1e-8 and pv > 0
        eq.loc[d] = pv
        expo.loc[d] = mv / pv
    return eq, expo, shares, px


def alloc_history(sched, day_slice):
    """MU每日权重×POSO → 参与度计算用。"""
    wmu = pd.Series(0.0, index=day_slice)
    exd = set(sched)
    cur_w = {s: 0.0 for s in TICKERS}
    s_i = idx.get_loc(day_slice[0])
    for k, d in enumerate(day_slice):
        i = s_i + k
        if d in exd:
            cur_w = dict(sched[d])
        sd = idx[i - 1] if i > 0 else d
        wmu.loc[d] = cur_w.get("MU", 0.0) * POSO["MU"].get(sd, 0.0)
    return wmu


def stats(eq, expo):
    r = eq.pct_change().dropna()
    return dict(final=float(eq.iloc[-1]), mdd=float((eq / eq.cummax() - 1).min()),
                sharpe=float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan)


CANDS = {"12m/price": (12, "price"), "12m/edge": (12, "edge"), "36m/price": (36, "price"), "36m/edge": (36, "edge")}
MI0 = 72
sd0 = idx[idx.to_period("M") == months[MI0].to_period("M")][0]
days_all = idx[idx.get_loc(sd0):]
print(f"=== 2×2矩阵(统一R3, 公共起点{days_all[0].date()}) ===")
hist = {}
for label, (win, kind) in CANDS.items():
    sched = build_schedule(MI0, len(months), win, kind)
    eq, expo, _, _ = run_pool(sched, days_all)
    st = stats(eq, expo)
    hist[label] = (sched, eq)
    out = [f"{label}: 全期{st['final']:.0f}/MDD{st['mdd']:.0%}/Sh{st['sharpe']:.2f}"]
    for bname, b0, b1 in [("2015-19", "2015-01-01", "2020-01-01"), ("2020-24", "2020-01-01", "2025-01-01"), ("2025-", "2025-01-01", None)]:
        seg = eq.loc[b0:b1] if b1 else eq.loc[b0:]
        if b1:
            seg = seg[seg.index < pd.Timestamp(b1)]
        out.append(f"{bname}:{float(seg.iloc[-1]/seg.iloc[0]-1):+.0%}")
    print(" | ".join(out))

print("\n=== 多起点固定5年 ===")
for yr in (2015, 2017, 2019, 2021):
    mi_s = next(i for i, m in enumerate(months) if m.year == yr and i >= 48)
    sd = idx[idx.to_period("M") == months[mi_s].to_period("M")][0]
    days = idx[idx.get_loc(sd):idx.get_loc(sd) + 1260]
    line = [str(yr)]
    for label, (win, kind) in CANDS.items():
        sched = build_schedule(mi_s, mi_s + 60, win, kind)
        eq, _, _, _ = run_pool(sched, days)
        line.append(f"{label}:{float(eq.iloc[-1]):.0f}")
    print(" | ".join(line))

print("\n=== MU九谷参与度(谷→MU首次获配, 滞后交易日/已涨/配置至周期顶捕获) ===")
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
i0 = idx.get_loc(days_all[0])
for label, (win, kind) in CANDS.items():
    sched, _ = hist[label]
    wmu = alloc_history(sched, days_all)
    row = [label]
    for t in qual:
        td = c.index[t]
        seg = wmu.loc[td:]
        first = next((d for d, v in seg.items() if v > 0.01), None)
        if first is None:
            row.append(f"{td.year}:未配")
            continue
        lag = idx.get_loc(first) - (idx.get_loc(td) if td in idx else i0)
        if td in idx:
            lag = idx.get_loc(first) - idx.get_loc(td)
        elapsed = c.loc[first] / c.iloc[t] - 1
        nxt_h = [i for k2, i2 in pivots if k2 == "H" and i2 > t]
        top_i = nxt_h[0] if nxt_h else n - 1
        cap = (c.loc[first:pd.Timestamp(c.index[top_i])].iloc[-1] if False else 0)
        segp = c.loc[first:c.index[top_i]]
        got = float(segp.iloc[-1] / c.loc[first] - 1) if len(segp) > 1 else 0.0
        cyc = float(c.index[top_i] and c.iloc[top_i] / c.iloc[t] - 1)
        row.append(f"{td.year}:滞后{lag}日/已涨{elapsed:.0%}/配后获{got:.0%}")
    print(" | ".join(row))
