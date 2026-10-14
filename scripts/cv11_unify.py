# -*- coding: utf-8 -*-
"""审查二十八轮: 统一机制+三P0修复。event_generator(事件源: MU=V4∪A∪B, 其余=A∪B; 60日去重) +
position_state_machine(事件日决策, 120日限时) 单一实现供单股/池级调用。归属用真实入场成交日(20/60/120窗, 一对一)。
时间块左闭右开。36m/edge升级检验(多起点+去最佳月)。Fast-V 2020专项诊断。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features, build_industry_features
import mu_af_v2 as M

D = os.path.join(config.DATA_DIR, "mu")
COST = 0.0015
HOLD = 120
DEDUP = 60
TICKERS = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]
closes, dp_ev, v4_ev = {}, {}, {}
for sym in TICKERS:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    dp = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    dp_ev[sym] = dp
    closes[sym] = c
v4tr = (M.v4.reindex(closes["MU"].index).fillna(0) > 0) & (M.v4.reindex(closes["MU"].index).fillna(0).diff() > 0)
v4_ev["MU"] = v4tr


def event_generator(sym, c):
    """P0-2统一: 事件源=MU: V4∪A∪B, 其余: A∪B; 60日去重 → 事件决策日列表。"""
    raw = dp_ev[sym].values.copy()
    if sym in v4_ev:
        raw = raw | v4_ev[sym].values
    evd, last = [], -9999
    for i, e in enumerate(raw):
        if e and i - last >= DEDUP:
            evd.append(i)
            last = i
    return evd


def position_state_machine(c, evd):
    """P0-2统一: 事件日=决策日(目标仓位1), 120日限时退出; 退出后仅新事件再入。返回目标仓位与交易列表(入场决策日索引)。"""
    n = len(c)
    pos = np.zeros(n)
    in_pos = False
    entry_i = -999
    trade_entries = []
    evset = set(evd)
    for i in range(n):
        if not in_pos and i in evset:
            in_pos, entry_i = True, i
            trade_entries.append(i)
        elif in_pos and i - entry_i >= HOLD:
            in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=c.index), trade_entries


def ledger_with_dates(px, pos, budget=100.0):
    cash, shares = float(budget), 0.0
    in_pos = False
    eq = pd.Series(np.nan, index=px.index)
    pxs = px.values
    ps = pos.reindex(px.index).fillna(0.0).values
    trades = []
    for i in range(1, len(px)):
        p = pxs[i]
        want = bool(ps[i - 1] >= 0.5)
        if want and not in_pos:
            shares = cash * (1 - COST) / p
            entry_fill = px.index[i]
            cash = 0.0
            in_pos = True
        elif (not want) and in_pos:
            cash = shares * p * (1 - COST)
            trades.append((entry_fill, px.index[i]))
            shares = 0.0
            in_pos = False
        eq.iloc[i] = cash + shares * p
    eq = eq.ffill().fillna(budget)
    if in_pos:
        trades.append((entry_fill, None))  # 未平仓
    return eq, trades


POSO, TRADE_IDX = {}, {}
for sym in TICKERS:
    p, te = position_state_machine(closes[sym], event_generator(sym, closes[sym]))
    POSO[sym] = p.reindex(idx) if False else p
POSO = pd.DataFrame({s: POSO[s] for s in TICKERS}).fillna(0.0)
idx = pd.concat(closes.values()).index.unique().sort_values()
POSO = POSO.reindex(idx).fillna(0.0)
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in TICKERS}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx).notna() for s in TICKERS})
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index
STRAT_MRET = pd.DataFrame({s: POSO[s].shift(1).fillna(0) * PX[s].pct_change().fillna(0) for s in TICKERS}).resample("ME").apply(lambda x: (1 + x).prod() - 1)

# ===== P0-1: MU逐谷归属(入场成交日, 一对一, 20/60/120窗) =====
c = closes["MU"]
pos, te = position_state_machine(c, event_generator("MU", c))
eq, trades = ledger_with_dates(c, pos)
piv_h = []
hi = lo = c.values[0]
trend = 0
lo_i = 0
pivots = []
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
print(f"=== P0-1 MU逐谷归属(入场成交日一对一, 事件源V4∪A∪B, 60日去重) ===")
print(f"账户交易{len(trades)}笔(含未平仓{sum(1 for _, x in trades if x is None)}) | 事后合格谷{len(qual)}个")
used = set()
for wdw in (20, 60, 120):
    used.clear()
    for ef, _ in trades:
        ei = c.index.get_loc(ef)
        cands = [(ei - t, t) for t in qual if t not in used and 0 <= ei - t <= wdw]
        if cands:
            used.add(min(cands)[1])
    print(f"入场后{wdw}日内覆盖: {len(used)}/{len(qual)}")
for t in qual:
    hit = next((ef for ef, _ in trades if 0 <= c.index.get_loc(ef) - t <= 120), None)
    print(f"  谷{c.index[t].date()}: {'入场' + str(hit.date()) if hit else '未覆盖'}")

# ===== P0-3: 池级三候选, 左闭右开时间块 =====
sys.path.insert(0, r"E:\mystockagent")
idx_all = pd.concat(closes.values()).index.unique().sort_values()
PXa = pd.DataFrame({s: closes[s].reindex(idx_all) for s in TICKERS}).ffill()
La = pd.DataFrame({s: closes[s].reindex(idx_all).notna() for s in TICKERS})
MCa = PXa.resample("ME").last()
mos = MCa.index
SMR = pd.DataFrame({s: POSO[s].shift(1).fillna(0) * PXa[s].pct_change().fillna(0) for s in TICKERS}).resample("ME").apply(lambda x: (1 + x).prod() - 1)


def score(sym, mi, win, kind):
    if kind == "price":
        w = MCa[sym].iloc[max(0, mi - win):mi].dropna()
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
        m = mos[mi]
        t_day = idx_all[idx_all.to_period("M") == m.to_period("M")][0]
        scores, act = {}, {}
        for s in TICKERS:
            sc = score(s, mi, win, kind)
            if np.isfinite(sc):
                scores[s] = sc
                act[s] = bool(POSO[s].reindex(idx_all[:idx_all.get_loc(t_day) + 1]).iloc[-1] >= 0.5)
        elig = {s: v for s, v in scores.items() if act.get(s)}
        w = {}
        if elig:
            rank = sorted(elig, key=elig.get, reverse=True)
            w = {rank[0]: 0.6} | ({rank[1]: 0.4} if len(rank) > 1 else {})
        sched[idx_all[idx_all.get_loc(t_day) + 1]] = w
    return sched


def run_pool(sched, day_slice, budget=100.0):
    cash = float(budget)
    shares = {s: 0.0 for s in TICKERS}
    cur_w = {s: 0.0 for s in TICKERS}
    exd = set(sched)
    eq = pd.Series(np.nan, index=day_slice)
    expo = pd.Series(np.nan, index=day_slice)
    s_i = idx_all.get_loc(day_slice[0])
    for k, d in enumerate(day_slice):
        i = s_i + k
        sd = idx_all[i - 1] if i > 0 else d
        if d in exd:
            cur_w = dict(sched[d])
        px = PXa.loc[d]
        for s in TICKERS:
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * POSO[s].get(sd, 0.0) * (cash + sum(shares[u] * px[u] for u in TICKERS if shares[u] > 0 and np.isfinite(px[u])))
            if np.isfinite(px[s]) and px[s] > 0 and La[s].loc[d] and tgt < cur_v:
                dv = tgt - cur_v
                cash -= dv + abs(dv) * COST
                shares[s] += dv / px[s]
        mv0 = sum(shares[s] * px[s] for s in TICKERS if shares[s] > 0 and np.isfinite(px[s]))
        pv = cash + mv0
        buys = {}
        for s in TICKERS:
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * POSO[s].get(sd, 0.0) * pv
            if np.isfinite(px[s]) and px[s] > 0 and La[s].loc[d] and tgt > cur_v:
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
    return eq, expo


MI0 = 72
sd0 = idx_all[idx_all.to_period("M") == mos[MI0].to_period("M")][0]
days_all = idx_all[idx_all.get_loc(sd0):]
print(f"\n=== P0-3 池级三候选(统一R3机制, 左闭右开块) ===")
for label, (win, kind) in {"36m/orig": (36, "price"), "60m/orig": (60, "price"), "36m/edge": (36, "edge")}.items():
    sched = build_schedule(MI0, len(mos), win, kind)
    eq, expo = run_pool(sched, days_all)
    out = [label]
    for bname, b0, b1 in [("2015-2019", "2015-01-01", "2020-01-01"), ("2020-2024", "2020-01-01", "2025-01-01"), ("2025-", "2025-01-01", None)]:
        seg = eq.loc[b0:b1] if b1 else eq.loc[b0:]
        seg = seg[seg.index < pd.Timestamp(b1)] if b1 else seg  # 右开
        r = seg.pct_change().dropna()
        out.append(f"{bname}: {float(seg.iloc[-1]/seg.iloc[0]-1):+.0%}/MDD{float((seg/seg.cummax()-1).min()):.0%}/Sh{float(r.mean()/r.std()*np.sqrt(252)) if r.std()>0 else np.nan:.2f}")
    print(" | ".join(out))

print("\n=== P1 36m/edge升级检验(多起点固定5年 vs 36m/orig) ===")
for yr in (2015, 2017, 2019, 2021):
    mi_s = next(i for i, m in enumerate(mos) if m.year == yr and i >= 48)
    mi_e = mi_s + 60
    sd = idx_all[idx_all.to_period("M") == mos[mi_s].to_period("M")][0]
    days = idx_all[idx_all.get_loc(sd):idx_all.get_loc(sd) + 1260]
    line = [str(yr)]
    for win, kind in ((36, "price"), (36, "edge")):
        sched = build_schedule(mi_s, mi_e, win, kind)
        eq, expo = run_pool(sched, days)
        r = eq.pct_change().dropna()
        line.append(f"{'price' if kind=='price' else 'edge'}:{float(eq.iloc[-1]):.0f}/-{-float((eq/eq.cummax()-1).min()):.0%}".replace("--", "-"))
    print(f"起点{yr}: " + " | ".join(line))
print("\n=== P1 去最佳月(公共起点2015-01, edge vs price) ===")
for win, kind in ((36, "price"), (36, "edge")):
    sched = build_schedule(MI0, len(mos), win, kind)
    eq, expo = run_pool(sched, days_all)
    mr = eq.resample("ME").last().pct_change().dropna()
    top = mr.nlargest(5)
    tot = np.log1p(mr).sum()
    rest3 = np.log1p(mr.drop(top.index[:3])).sum()
    print(f"{kind}: 全期{np.expm1(tot):+.0%} 去Top3月{np.expm1(rest3):+.0%} Top月{[str(t)[:7] for t in top.index[:3]]}")

print("\n=== P1 Fast-V 2020专项诊断 ===")
c = closes["MU"]
f = build_features(c, [])
dd = f["dd252"]
m10 = c.rolling(10).mean()
crash = (dd.diff(10) <= -0.15)
w20 = c.loc["2020-02-01":"2020-06-30"]
seg = pd.DataFrame({"close": w20, "MA10": m10.loc[w20.index], "dd252": dd.loc[w20.index], "急跌": crash.loc[w20.index], "收复MA10": (c > m10).loc[w20.index]})
print(seg.iloc[::5].round(2).to_string())
fpath = os.path.join(D, "mu_fund.csv")
ind = build_industry_features(fpath, c.index)
fy = ind["rev_yoy"].loc["2020-02-01":"2020-06-30"].dropna()
print(f"SEC rev_yoy可得日(2020-02~06): " + ", ".join(f"{d.date()}:{v:.0%}" for d, v in fy.items()))
