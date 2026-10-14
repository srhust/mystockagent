# -*- coding: utf-8 -*-
"""审查三十二轮: ①P0 MU日期对齐+交易ID一对一归属(解释2010-08判定翻转) ②P0配对bootstrap修正(同期联合抽样,
两策略分别复利后取差 + 月均配对差CI)。"""
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
c = pd.read_csv(os.path.join(D, "MU.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
f = build_features(c, [])
dd = f["dd252"]
ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
dp = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
v4tr = (M.v4.reindex(c.index).fillna(0) > 0) & (M.v4.reindex(c.index).fillna(0).diff() > 0)
raw = dp.values | v4tr.values
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
ps = pos  # 与c.index逐位对齐(同一循环构建, 无reindex)


def ledger_trades(pxs, ps):
    """T+1账本, 返回交易列表(入场成交日索引, 退出成交日索引或None)。"""
    cash, shares, in_pos = 100.0, 0.0, False
    trades = []
    for i in range(1, n):
        want = bool(ps[i - 1] >= 0.5)
        if want and not in_pos:
            shares, cash, in_pos = cash * (1 - COST) / pxs[i], 0.0, True
            efill = i
        elif (not want) and in_pos:
            cash, shares, in_pos = shares * pxs[i] * (1 - COST), 0.0, False
            trades.append((efill, i))
        elif want and in_pos and False:
            pass
    if in_pos:
        trades.append((efill, None))  # 未平仓
    return trades


trades = ledger_trades(c.values, ps)
pivots = []
hi = lo = c.values[0]
trend = 0
lo_i = 0
for i in range(1, n):
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

print("=== P0-a MU九谷归属(单标的账本, MU自身索引, 交易ID一对一) ===")
print(f"账户交易{len(trades)}笔(未平仓{sum(1 for _, x in trades if x is None)})")
trade_of = {}
for tid, (ef, xf) in enumerate(trades):
    if xf is None:
        continue
    # 一对一: 每笔交易归入入场成交日之前最近的合格谷(入场在该谷后240日内)
    cands = [(ef - t, t) for t in qual if 0 <= ef - t <= 240]
    if cands:
        t = min(cands)[1]
        trade_of.setdefault(t, []).append(tid)
for t in qual:
    tids = trade_of.get(t, [])
    if tids:
        ef = trades[tids[0]][0]
        lag = ef - t
        pre = bool(ps[max(0, t - 1)] >= 0.5)
        state = "持仓穿越" if pre else "新入场"
        e = t
        if pre:
            e = t - 1
            while e > 0 and ps[e - 1] >= 0.5:
                e -= 1
            cost_px = c.values[e]
            mae = float(min(c.values[e:min(n, e + HOLD)]) / cost_px - 1)
            print(f"谷{c.index[t].date()}: {state}(入场{c.index[e].date()}, 谷内MAE{mae:.0%}), 关联交易{len(tids)}笔")
        else:
            print(f"谷{c.index[t].date()}: 新入场(成交{c.index[ef].date()}, 谷后{lag}交易日), 关联交易{len(tids)}笔")
    else:
        pre = bool(ps[max(0, t - 1)] >= 0.5)
        print(f"谷{c.index[t].date()}: {'持仓中(穿越)' if pre else '完全错过'}")
print("注: 一笔交易可覆盖多个观察窗(相邻谷), 但交易ID唯一; 覆盖谷数=有账户关联的谷数, 与独立交易数分开报告")

print("\n=== P0-b 配对bootstrap修正(同期联合抽样, 分别复利取差) ===")
TICKERS = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]
closes, dp_ev, v4_ev = {}, {}, {}
for sym in TICKERS:
    cc = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    ff = build_features(cc, [])
    ddx = ff["dd252"]
    m20, m50 = cc.rolling(20).mean(), cc.rolling(50).mean()
    st250 = (ddx <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    dp_ev[sym] = (((cc > m20) & (m20 > m20.shift(5)) & st250 & (ddx <= -0.40) & (cc <= m50 * 1.05)) | ((m50 > m50.shift(10)) & (ddx >= -0.30) & (ddx <= -0.10)))
    closes[sym] = cc
v4_ev["MU"] = v4tr


def pos_unified(sym):
    cc = closes[sym]
    rw = dp_ev[sym].values.copy()
    if sym in v4_ev:
        rw = rw | v4_ev[sym].values
    nn = len(cc)
    p = np.zeros(nn)
    ip = False
    ei = -999
    la = -9999
    for i in range(nn):
        if rw[i] and i - la >= DEDUP:
            la = i
        if not ip and rw[i] and i == la:
            ip, ei = True, i
        elif ip and i - ei >= HOLD:
            ip = False
        p[i] = 1.0 if ip else 0.0
    return pd.Series(p, index=cc.index)


def ledger_nav(cc, p):
    cash, shares = 100.0, 0.0
    nav = pd.Series(np.nan, index=cc.index)
    pxs = cc.values
    pv = p.reindex(cc.index).fillna(0.0).values
    ip = False
    for i in range(1, len(cc)):
        want = bool(pv[i - 1] >= 0.5)
        if want and not ip:
            shares, cash, ip = cash * (1 - COST) / pxs[i], 0.0, True
        elif (not want) and ip:
            cash, shares, ip = shares * pxs[i] * (1 - COST), 0.0, False
        nav.iloc[i] = cash + shares * pxs[i]
    return nav.ffill().fillna(100.0)


NAV = {s: ledger_nav(closes[s], pos_unified(s)) for s in TICKERS}
idx = pd.concat(closes.values()).index.unique().sort_values()
POSO = pd.DataFrame({s: pos_unified(s).reindex(idx) for s in TICKERS}).fillna(0.0)
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in TICKERS}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx).notna() for s in TICKERS})
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index
SMR = pd.DataFrame({s: NAV[s].reindex(idx).resample("ME").last().pct_change() for s in TICKERS}).reindex(months).fillna(0.0)


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
runs = {}
for label, (win, kind) in {"12m/edge": (12, "edge"), "36m/edge": (36, "edge"), "36m/price": (36, "price")}.items():
    runs[label] = run_pool(build_schedule(MI0, len(months), win, kind), days_all)
mret = {k: v.resample("ME").last().pct_change().dropna() for k, v in runs.items()}
common = mret["12m/edge"].index.intersection(mret["36m/edge"].index).intersection(mret["36m/price"].index)
M12, M36, MP = mret["12m/edge"][common].values, mret["36m/edge"][common].values, mret["36m/price"][common].values

for tag, (A, B) in {"36m/edge−36m/price": (M36, MP), "12m/edge−36m/edge": (M12, M36)}.items():
    lo_t = 100.0
    hi_t = 100.0
    wealth_A = float(np.prod(1 + A))
    wealth_B = float(np.prod(1 + B))
    rng = np.random.default_rng(7)
    L = 12
    diffs = []
    for _ in range(500):
        starts = rng.integers(0, len(A), len(A) // L + 1)
        sel = np.concatenate([starts[k] + np.arange(L) for k in range(len(starts))])[:len(A)] % len(A)
        wa = float(np.prod(1 + A[sel]))
        wb = float(np.prod(1 + B[sel]))
        diffs.append(wa / wb - 1)
    lo, hi = np.percentile(diffs, 5), np.percentile(diffs, 95)
    md = (A - B).mean()
    se = (A - B).std() / np.sqrt(len(A)) * np.sqrt(L)  # 月均差的自相关调整(粗)
    print(f"{tag}: 财富比点估计{wealth_A/wealth_B-1:+.0%} bootstrap(12月块500次)CI[5%,95%]: [{lo:+.0%}, {hi:+.0%}]; 月均配对差{md:+.2%}(粗t≈{md/se:.2f})")
