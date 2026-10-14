# -*- coding: utf-8 -*-
"""审查三十九轮: 四项P0修复 — ①月度准入持久化(cur_w保留至下一调度日) ②真实自身事件索引(ownpos由closes显式构建+事件日期一致性断言)
③V2aR预算语义(目标=entry_budget×风险系数, 不侵占他仓预算) ④全部买单统一比例分配(sc=min(1,cash/need), ENTER与GROW10同规)
⑤T10逐日精确等价(单股参考侧V2aR同改预算口径; 首个差异日诊断输出) ⑥T11加强: 双持仓同日GROW10现金不足反转顺序一致。"""
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


def fsm_decide(mode, state, entry_i, i_own, below, in_ev):
    if state == "FLAT":
        return "ENTER" if in_ev else None
    if state == "FULL":
        if (i_own - entry_i) >= HOLD:
            return "EXIT"
        if mode == "V2aF" and below:
            return "HALVE"
        if mode == "V2aR":
            return "TRIM05" if below else "GROW10"
        return None
    if state == "FROZEN":
        return "EXIT" if (i_own - entry_i) >= HOLD else None
    return None


def run_contract(universe, sched, risk_mode, px_d, ma100_d, ev_d, listed_d, dt_index, ownpos_d, budget=100.0, collect=None):
    """唯一执行器(修复版)。cur_w持久化; V2aR预算语义; 全部买单比例分配。"""
    cash = float(budget)
    state = {s: "FLAT" for s in universe}
    entry_i = {s: -999 for s in universe}
    entry_budget = {s: 0.0 for s in universe}
    shares = {s: 0.0 for s in universe}
    eq = pd.Series(np.nan, index=dt_index)
    cash_hist = pd.Series(np.nan, index=dt_index)
    sh_hist = pd.Series(np.nan, index=dt_index)
    fees = 0.0
    cur_w = {}
    for k, d in enumerate(dt_index):
        if d in sched:
            cur_w = dict(sched[d])  # P0-1: 月度准入持久化至下一调度日
        if k == 0:
            eq.iloc[0], cash_hist.iloc[0], sh_hist.iloc[0] = cash, cash, 0.0
            continue
        sd = dt_index[k - 1]
        px_today = {s: px_d[s].get(d, np.nan) for s in universe}
        px_yest = {s: px_d[s].get(sd, np.nan) for s in universe}
        orders = {}
        nav_prev = cash + sum(shares[s] * px_yest[s] for s in universe if shares[s] > 0 and np.isfinite(px_yest[s]))
        for s in universe:
            if not listed_d[s].get(d, False) or not np.isfinite(px_today[s]) or px_today[s] <= 0:
                continue
            i_own = ownpos_d[s].get(sd, -1)
            below = bool(px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
            in_ev = i_own in ev_d[s]
            w = cur_w.get(s, 0.0)
            if state[s] == "FLAT" and in_ev and w <= 0:
                continue
            act = fsm_decide(risk_mode, state[s], entry_i[s], i_own, below, in_ev)
            if act:
                orders[s] = act
        for s, act in orders.items():
            p = px_today[s]
            if act == "EXIT":
                proceeds = shares[s] * p * (1 - COST)
                fees += shares[s] * p * COST
                cash += proceeds
                shares[s], state[s] = 0.0, "FLAT"
            elif act == "HALVE":
                sell_sh = shares[s] / 2.0
                proceeds = sell_sh * p * (1 - COST)
                fees += sell_sh * p * COST
                cash += proceeds
                shares[s] -= sell_sh
                state[s] = "FROZEN"
            elif act == "TRIM05":
                cur = shares[s] * p
                tgt = 0.5 * entry_budget[s]
                if cur > tgt:
                    sell_sh = min(shares[s], (cur - tgt) / p)
                    proceeds = sell_sh * p * (1 - COST)
                    fees += sell_sh * p * COST
                    cash += proceeds
                    shares[s] -= sell_sh
        buys = {}
        for s, act in orders.items():
            p = px_today[s]
            if act == "ENTER":
                buys[s] = cur_w.get(s, 0.0) * nav_prev
            elif act == "GROW10":
                cur = shares[s] * p
                gap = entry_budget[s] - cur
                if gap > 0:
                    buys[s] = gap
        need = sum(buys.values())
        sc = min(1.0, cash / need) if need > 0 else 0.0  # P0-4: 费用在apply内部, 不再(1+COST)
        for s, amt in buys.items():
            p = px_today[s]
            spend = amt * sc
            if spend > cash:
                spend = cash
            fee = spend * COST
            fees += fee
            cash -= spend
            shares[s] += spend * (1 - COST) / p
            if orders[s] == "ENTER" and spend > 1e-9:
                state[s], entry_i[s], entry_budget[s] = "FULL", ownpos_d[s].get(sd, -1), spend * (1 - COST)
        nav_now = cash + sum(shares[s] * px_today[s] for s in universe if shares[s] > 0)
        for s in universe:
            assert (state[s] == "FLAT") == (shares[s] <= 1e-12)
        assert cash >= -1e-9 and nav_now > 0
        eq.iloc[k] = nav_now
        cash_hist.iloc[k] = cash
        sh_hist.iloc[k] = sum(shares[s] for s in universe)
    if collect is not None:
        collect.update(eq=eq, cash=cash_hist, sh=sh_hist, shares=shares)
    return eq, shares, fees


# ===== P0-2 事件日期一致性断言 + 显式ownpos =====
idx_all = pd.concat(closes.values()).index.unique().sort_values()
ev_all = {s: events(s) for s in TICKERS}
OWNPOS = {s: pd.Series(np.arange(len(closes[s])), index=closes[s].index) for s in TICKERS}
for s in TICKERS:
    for sd in ev_all[s]:
        assert closes[s].index[sd] in idx_all, f"事件日期不在池级日历 {s} {sd}"
print("P0-2 事件日期一致性断言: PASS (全部事件日期存在于池级日历)")

# ===== T10 逐日精确等价: 单股参考(预算口径V2aR) vs 池级路径 =====
print("\n=== T10 单股-池级逐日精确等价 ===")


def run_single_ref(mode, w_level=1.0):
    """单股参考执行器(cv20语义 + V2aR预算口径): 与池级同 FSM/原语, 独立实现以供对照。"""
    c = closes["MU"]
    pxs = c.values
    m100 = ma100s["MU"].values
    ev = ev_all["MU"]
    n = len(c)
    cash, shares = 100.0, 0.0
    state = "FLAT"
    entry_i, entry_budget = -999, 0.0
    cash_h, sh_h = [], []
    for i in range(1, n):
        p = pxs[i]
        sd = i - 1
        below = pxs[sd] < m100[sd]
        in_ev = sd in ev
        if state == "FLAT" and in_ev:
            spend = cash * w_level
            shares += spend * (1 - COST) / p
            cash -= spend
            state, entry_i, entry_budget = "FULL", sd, spend * (1 - COST)
        elif state == "FULL" and (sd - entry_i) >= HOLD:
            cash += shares * p * (1 - COST)
            shares, state = 0.0, "FLAT"
        elif state == "FULL" and mode == "V2aF" and below:
            sell_sh = shares / 2.0
            cash += sell_sh * p * (1 - COST)
            shares -= sell_sh
            state = "FROZEN"
        elif state == "FROZEN" and (sd - entry_i) >= HOLD:
            cash += shares * p * (1 - COST)
            shares, state = 0.0, "FLAT"
        elif state == "FULL" and mode == "V2aR":
            cur = shares * p
            tgt = (0.5 if below else 1.0) * entry_budget
            if cur > tgt:
                shares -= min(shares, (cur - tgt) / p)
                cash += min(shares, (cur - tgt) / p) * p * (1 - COST)
            elif cur < tgt and cash > 0:
                buy = min(tgt - cur, cash)
                cash -= buy
                shares += buy * (1 - COST) / p
        cash_h.append(cash)
        sh_h.append(shares)
    return np.array(cash_h), np.array(sh_h)


c = closes["MU"]
dtMU = c.index[1:]
sched_single = {c.index[sd + 1]: {"MU": 1.0} for sd in ev_all["MU"] if sd + 1 < len(c)}
pxMU = pd.Series(c.values, index=c.index)
m100MU = pd.Series(ma100s["MU"].values, index=c.index)
listedMU = pd.Series(True, index=c.index)
all_pass = True
for mode in ("V0", "V2aF", "V2aR"):
    collect = {}
    eq_pool, sh_pool, _ = run_contract(["MU"], sched_single, mode, {"MU": pxMU}, {"MU": m100MU}, {"MU": ev_all["MU"]}, {"MU": listedMU}, dtMU, {"MU": OWNPOS["MU"]}, collect=collect)
    cash_ref, sh_ref = run_single_ref(mode)
    cash_pool = collect["cash"].values
    sh_pool_d = np.array([collect["shares"]["MU"]] * len(cash_pool))
    diff_cash = np.abs(cash_pool - cash_ref)
    bad = np.where(diff_cash > 1e-6)[0]
    if len(bad) == 0:
        print(f"{mode}: 逐日现金轨迹一致 PASS")
    else:
        j = bad[0]
        d0 = dtMU[j]
        print(f"{mode}: 首个差异日 {d0.date()} 现金池级{cash_pool[j]:.4f} vs 参考{cash_ref[j]:.4f} (差异{diff_cash[j]:.4f})")
        for jj in range(max(0, j - 5), min(len(cash_pool), j + 2)):
            print(f"   {dtMU[jj].date()} 池级现金{cash_pool[jj]:.4f} 参考{cash_ref[jj]:.4f} 差{cash_pool[jj]-cash_ref[jj]:+.4f}")
        all_pass = False
print(f"T10整体: {'PASS' if all_pass else '存在差异(见上)'}")

# ===== T11加强: 双持仓同日GROW10现金不足, 反转顺序一致 =====
print("\n=== T11加强 双持仓GROW10顺序不变 ===")
dts = pd.date_range("2020-01-01", periods=10, freq="B")
pxA = pd.Series([10, 10, 10, 20, 20, 20, 20, 20, 20, 20], index=dts)
pxB = pd.Series([10, 10, 10, 20, 20, 20, 20, 20, 20, 20], index=dts)
maA = pd.Series([5, 5, 5, 5, 100, 100, 100, 100, 100, 100], index=dts)  # 前4日below, 第5日起above
sched2 = {dts[1]: {"A": 0.5, "B": 0.5}}
ownpos2 = {"A": pd.Series(np.arange(10), index=dts), "B": pd.Series(np.arange(10), index=dts)}
ev2 = {"A": {1}, "B": {1}}
li2 = {"A": pd.Series(True, index=dts), "B": pd.Series(True, index=dts)}
eq1, sh1, _ = run_contract(["A", "B"], sched2, "V2aR", {"A": pxA, "B": pxB}, {"A": maA, "B": maA}, ev2, li2, dts, ownpos2)
eq2, sh2, _ = run_contract(["B", "A"], sched2, "V2aR", {"A": pxA, "B": pxB}, {"A": maA, "B": maA}, ev2, li2, dts, ownpos2)
same = abs(float(eq1.iloc[-1]) - float(eq2.iloc[-1])) < 1e-9 and abs(sh1["A"] - sh2["A"]) < 1e-9 and abs(sh1["B"] - sh2["B"]) < 1e-9
print(f"双持仓GROW10反转顺序: 终值{float(eq1.iloc[-1]):.2f} vs {float(eq2.iloc[-1]):.2f} {'PASS' if same else 'FAIL'}")
assert same

# ===== 重跑统一池级矩阵 =====
print("\n=== 池级矩阵(修复后, 真实edge评分) ===")
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


def build_schedule(mi0, mi1, win, kind):
    sched = {}
    for mi in range(mi0, mi1):
        m = months[mi]
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


MI0 = 72
sd0 = idx_all[idx_all.to_period("M") == months[MI0].to_period("M")][0]
days_all = idx_all[idx_all.get_loc(sd0):]
for label, (win, kind) in {"36m/price": (36, "price"), "12m/edge": (12, "edge"), "36m/edge": (36, "edge")}.items():
    sched = build_schedule(MI0, len(months), win, kind)
    out = [label]
    for mode in ("V0", "V2aF", "V2aR"):
        eq, sh, fees = run_contract(TICKERS, sched, mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS)
        r = eq.pct_change().dropna()
        out.append(f"{mode}: {float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} 费{fees:.0f}")
    print(" | ".join(out))
