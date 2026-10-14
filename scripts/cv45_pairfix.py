# -*- coding: utf-8 -*-
"""审查四十五轮: ①配对超额索引修复(同日历日期get_loc, 禁止跨序列整数位置) ②FIFO来源守恒(RESERVE_RELEASE入队+每日来源合计=现金断言) — 均在正式模块cycle_system/cycle_portfolio.py内 ③回归: T-D/T-C合成测试+冻结终值+池级矩阵重算。"""
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
from cycle_portfolio import fsm_decide, v2ar_target, run_contract  # 正式模块(唯一实现源)

D = os.path.join(config.DATA_DIR, "mu")
COST, HOLD, DEDUP = 0.0015, 120, 60
TICKERS = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]
closes, dp_ev, v4_ev, ma100s = {}, {}, {}, {}
for sym in TICKERS:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    dp_ev[sym] = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    ma100s[sym] = c.rolling(100).mean()
    closes[sym] = c
v4tr = (M.v4.reindex(closes["MU"].index).fillna(0) > 0) & (M.v4.reindex(closes["MU"].index).fillna(0).diff() > 0)
v4_ev["MU"] = v4tr


def events(sym):
    rw = dp_ev[sym].values.copy()
    if sym in v4_ev:
        rw = rw | v4_ev[sym].values
    out, last = set(), -9999
    for i, e in enumerate(rw):
        if e and i - last >= DEDUP:
            out.add(i)
            last = i
    return out


ev_all = {s: events(s) for s in TICKERS}
OWNPOS = {s: pd.Series(np.arange(len(closes[s])), index=closes[s].index) for s in TICKERS}
idx_all = pd.concat(closes.values()).index.unique().sort_values()

# ===== T-D as-of + T-C EXIT成交日期 (正式模块回归) =====
print("=== 合成回归(T-D/T-C, 正式模块) ===")
dts4 = pd.date_range("2022-01-03", periods=8, freq="B")
pxA = pd.Series([10, 10, np.nan, 20, 20, 20, 20, 20], index=dts4)
maA = pd.Series([1000.0, 1000.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0], index=dts4)
pxB, pxD = pd.Series(10.0, index=dts4), pd.Series(10.0, index=dts4)
maB = pd.Series(1000.0, index=dts4)
snap4 = {(d.to_period("M"), s): 1.0 for d in dts4 for s in ("A", "B", "D")}
own4 = {s: pd.Series(np.arange(8), index=dts4) for s in ("A", "B", "D")}
collect = {}
run_contract(["A", "B", "D"], snap4, "V2aF", {"A": pxA, "B": pxB, "D": pxD}, {"A": maA, "B": maB, "D": maB}, {"A": {0}, "B": {2}, "D": {2}}, {"A": pd.Series(True, index=dts4), "B": pd.Series(True, index=dts4), "D": pd.Series(True, index=dts4)}, dts4, own4, collect=collect)
qB = [f[4] for f in collect["fills"] if f[0] == "B" and f[3] == "ENTER"]
okD = len(qB) == 1 and 3.9 < qB[0] < 4.05
print(f"T-D as-of: B成交{qB[0] if qB else 0:.3f} {'PASS' if okD else 'FAIL'}")
assert okD
n = 130
dts3 = pd.date_range("2021-01-04", periods=n, freq="B")
px3 = pd.Series(10.0, index=dts3)
px3.iloc[122] = np.nan
ma3 = pd.Series(1000.0, index=dts3)
snap3 = {(d.to_period("M"), "MU"): 1.0 for d in dts3}
collect = {}
eq3, _, _ = run_contract(["MU"], snap3, "V0", {"MU": px3}, {"MU": ma3}, {"MU": {1}}, {"MU": pd.Series(True, index=dts3)}, dts3, {"MU": pd.Series(np.arange(n), index=dts3)}, collect=collect)
ex = [f for f in collect["fills"] if f[3] == "EXIT"]
shM = collect["sh"]["MU"]
okC = (len(ex) == 1 and ex[0][1] == dts3[121] and ex[0][2] == dts3[123] and shM.loc[dts3[122]] > 0 and shM.loc[dts3[123]] == 0 and abs(float(eq3.iloc[-1]) - 99.70) < 0.01)
print(f"T-C EXIT: signal={ex[0][1].date()} fill={ex[0][2].date()} {'PASS' if okC else 'FAIL'}")
assert okC

# ===== 池级矩阵 + 修复后配对超额 =====
print("\n=== 池级矩阵(正式模块) ===")
PX = pd.DataFrame({s: closes[s].reindex(idx_all) for s in TICKERS}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx_all).notna() for s in TICKERS})
PXd = {s: pd.Series(PX[s].values, index=idx_all) for s in TICKERS}
MA100d = {s: pd.Series(ma100s[s].reindex(idx_all).values, index=idx_all) for s in TICKERS}
LISTd = {s: LISTED[s] for s in TICKERS}
POSO = {}
for s in TICKERS:
    ev2 = ev_all[s]
    cc = closes[s]
    nn = len(cc)
    p = np.zeros(nn)
    ip = False
    e_i = -999
    for i in range(nn):
        if not ip and i in ev2:
            ip, e_i = True, i
        elif ip and i - e_i >= HOLD:
            ip = False
        p[i] = 1.0 if ip else 0.0
    POSO[s] = pd.Series(p, index=cc.index)


def ledger_nav(cc, p):
    cash, shares = 100.0, 0.0
    nav = pd.Series(np.nan, index=cc.index)
    pv = p.reindex(cc.index).fillna(0.0).values
    pxs2 = cc.values
    ip = False
    for i in range(1, len(cc)):
        want = bool(pv[i - 1] >= 0.5)
        if want and not ip:
            shares, cash, ip = cash * (1 - COST) / pxs2[i], 0.0, True
        elif (not want) and ip:
            cash, shares, ip = shares * pxs2[i] * (1 - COST), 0.0, False
        nav.iloc[i] = cash + shares * pxs2[i]
    return nav.ffill().fillna(100.0)


NAV = {s: ledger_nav(closes[s], POSO[s].reindex(closes[s].index)) for s in TICKERS}
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index
SMR = pd.DataFrame({s: NAV[s].reindex(idx_all).resample("ME").last().pct_change() for s in TICKERS}).reindex(months).fillna(0.0)


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


MI0 = 72
sd0 = idx_all[idx_all.to_period("M") == months[MI0].to_period("M")][0]
days_all = idx_all[idx_all.get_loc(sd0):]


def paired_rel(rejs, eq_pool):
    """P0-1修复: 同日历日期(get_loc), 两端日期必须同时有效。"""
    diffs = []
    for s, d, p0 in rejs:
        try:
            j = eq_pool.index.get_loc(d)
        except KeyError:
            continue
        if j + 120 >= len(eq_pool):
            continue
        end_date = eq_pool.index[j + 120]
        p1 = PX[s].at[end_date] if end_date in PX[s].index else np.nan
        if not (np.isfinite(p0) and np.isfinite(p1) and p0 > 0 and p1 > 0):
            continue
        stock_ret = p1 / p0 - 1
        pool_ret = eq_pool.iloc[j + 120] / eq_pool.iloc[j] - 1
        diffs.append(stock_ret - pool_ret)
    v = np.array(diffs)
    v = v[np.isfinite(v)]
    return np.mean(v), np.median(v), float((v > 0).mean()), len(v)


for label, (win, kind) in {"36m/price": (36, "price"), "12m/edge": (12, "edge"), "36m/edge": (36, "edge")}.items():
    snap = {}
    for mi in range(1, len(months)):
        for s in TICKERS:
            snap[(months[mi].to_period("M"), s)] = score(s, mi, win, kind)
    row = [label]
    for mode, rsv in [("V0", False), ("V2aF-Reserve", True), ("V2aF", False), ("V2aR", False)]:
        collect = {}
        eq, sh, fees = run_contract(TICKERS, snap, "V2aF" if "V2aF" in mode else mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect, reserve_mode=rsv)
        f = collect["funnel"]
        r = eq.pct_change().dropna()
        row.append(f"{mode}: {float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} N4={f['n4']} HALVE资助={f['halve_funded_n']}笔/{f['halve_funded_amt']:.0f}")
        if mode == "V0":
            m, md, wr, n_ = paired_rel(collect["rejs"], eq)
            row.append(f"配对超额120日均{m:+.1%} 中位{md:+.1%} 胜率{wr:.0%}(n={n_})")
    print(" | ".join(row))
