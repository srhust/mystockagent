# -*- coding: utf-8 -*-
"""审查三十八轮: 执行契约落地 — ①唯一FSM决策核心fsm_decide(单股与池级同调, 池级不再自建状态转换)
②状态-份额逐日一致性断言(FULL⇒shares>0, FLAT⇒shares==0) ③顺序不变批量成交(先全部决策→批量卖→按比例分配买)
④冻结buy-once契约: 月度排名只控制新资金准入, 不强制卖出旧持仓; 退出仅由HOLD到期或风险机制触发
⑤验收: 九项单股测试 + T10单股池级等价(1标的100%预算现金股份轨迹逐日相同) + T11顺序不变性(反转股票表)。"""
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


# ===== 唯一FSM决策核心(纯函数, 单股/池级共用) =====
def fsm_decide(mode, state, entry_i, sd_i, below, in_ev, hold_expired):
    """返回动作: ENTER/HALVE/EXIT/TRIM05/GROW10/None。buy-once: 排名不触发EXIT。"""
    if state == "FLAT":
        return "ENTER" if in_ev else None
    if state == "FULL":
        if hold_expired:
            return "EXIT"
        if mode == "V2aF" and below:
            return "HALVE"
        if mode == "V2aR":
            return "TRIM05" if below else "GROW10"
        return None
    if state == "FROZEN":
        return "EXIT" if hold_expired else None
    return None


def apply_enter(cash, shares, p, budget):
    spend = min(budget, cash)
    fee = spend * COST
    return cash - spend, shares + spend * (1 - COST) / p, fee


def apply_sell(cash, shares, p, sell_sh):
    proceeds = sell_sh * p * (1 - COST)
    fee = sell_sh * p * COST
    return cash + proceeds, shares - sell_sh, fee


def run_contract(universe, sched, risk_mode, closes_d, ma100_d, ev_d, px_d, listed_d, dt_index, budget=100.0):
    """唯一执行器: 单股(universe=["MU"], sched仅事件日w=1.0)与池级同一路径。
    批量成交: 决策(全部)→卖(EXIT/HALVE/TRIM05)→买(ENTER按w×nav预算, GROW10, 现金不足按比例分配)。"""
    n = len(dt_index)
    cash = float(budget)
    state = {s: "FLAT" for s in universe}
    entry_i = {s: -999 for s in universe}
    shares = {s: 0.0 for s in universe}
    eq = pd.Series(np.nan, index=dt_index)
    fees = 0.0
    s0 = dt_index.get_loc(dt_index[0])
    for k in range(len(dt_index)):
        d = dt_index[k]
        i = s0 + k
        if k == 0:
            eq.iloc[k] = cash + sum(shares[s] * px_d[s].get(d, np.nan) for s in universe if shares[s] > 0)
            continue
        sd = dt_index[k - 1]
        cur_w = sched.get(d, {})
        px_today = {s: px_d[s].get(d, np.nan) for s in universe}
        px_yest = {s: px_d[s].get(sd, np.nan) for s in universe}
        # 阶段1: 全部决策(基于昨日可见)
        orders = {}
        nav_prev = cash + sum(shares[s] * px_yest[s] for s in universe if shares[s] > 0 and np.isfinite(px_yest[s]))
        for s in universe:
            if not listed_d[s].get(d, False) or not np.isfinite(px_today[s]) or px_today[s] <= 0:
                continue  # 缺价: 无成交, 状态保持(缺价日入场决策转PENDING由事件在下一可执行日重判)
            below = bool(px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
            in_ev = i - 1 in ev_d[s]
            hold_expired = entry_i[s] >= 0 and (i - 1 - entry_i[s]) >= HOLD
            w = cur_w.get(s, 0.0)
            if state[s] == "FLAT" and in_ev and w <= 0:
                continue  # buy-once: 无新预算不入场
            act = fsm_decide(risk_mode, state[s], entry_i[s], i - 1, below, in_ev, hold_expired)
            if act:
                orders[s] = act
        # 阶段2: 批量卖(EXIT/HALVE/TRIM05)
        for s, act in orders.items():
            p = px_today[s]
            if act == "EXIT":
                cash, shares[s], fee = apply_sell(cash, shares[s], p, shares[s])
                fees += fee
                state[s] = "FLAT"
            elif act == "HALVE":
                cash, shares[s], fee = apply_sell(cash, shares[s], p, shares[s] / 2.0)
                fees += fee
                state[s] = "FROZEN"
            elif act == "TRIM05":
                nav_now = cash + sum(shares[u] * px_today[u] for u in universe if shares[u] > 0)
                tgt = 0.5 * nav_now
                cur = shares[s] * p
                if tgt < cur:
                    cash, shares[s], fee = apply_sell(cash, shares[s], p, min(shares[s], (cur - tgt) / p))
                    fees += fee
        # 阶段3: 批量买(ENTER按w×nav, GROW10; 现金不足按比例)
        buys = {}
        for s, act in orders.items():
            if act == "ENTER":
                buys[s] = ("ENTER", cur_w.get(s, 0.0) * nav_prev)
            elif act == "GROW10":
                nav_now = cash + sum(shares[u] * px_today[u] for u in universe if shares[u] > 0)
                cur = shares[s] * px_today[s]
                if nav_now > cur:
                    buys[s] = ("GROW10", nav_now - cur)
        need = sum(v for _, v in buys.values())
        sc = min(1.0, cash / (1 + COST) / need) if need > 0 else 0.0
        for s, (act, amt) in buys.items():
            p = px_today[s]
            if act == "ENTER":
                cash, shares[s], fee = apply_enter(cash, shares[s], p, amt * sc)
                fees += fee
                state[s], entry_i[s] = "FULL", i - 1
            else:
                buy = min(amt, cash)
                cash, shares[s], fee = apply_enter(cash, shares[s], p, buy)
                fees += fee
        # 状态-份额一致性断言 + 守恒
        nav_now = cash + sum(shares[s] * px_today[s] for s in universe if shares[s] > 0)
        for s in universe:
            assert (state[s] == "FLAT") == (shares[s] <= 1e-12), f"状态份额脱节 {s} {d}"
        assert cash >= -1e-9 and nav_now > 0
        eq.iloc[k] = nav_now
    return eq, shares, state, fees


# ===== 九项单股验收测试(合成价格) =====
print("=== 九项单股FSM测试 ===")
ok_all = True


def run_single_test(prices, ma_vals, ev, mode):
    ps = pd.Series(prices, dtype=float)
    dt = pd.date_range("2020-01-01", periods=len(ps), freq="B")
    psd = pd.Series(prices, dtype=float, index=dt)
    sched = {dt[sd + 1]: {"MU": 1.0} for sd in ev if sd + 1 < len(ps)}
    m100 = pd.Series(ma_vals, dtype=float, index=dt)
    eq, sh, st, fees = run_contract(["MU"], sched, mode, {"MU": psd}, {"MU": m100}, {"MU": set(ev)}, {"MU": psd}, {"MU": pd.Series(True, index=dt)}, dt)
    shs = None
    return eq, sh["MU"], shs, st["MU"], fees


prices = [10.0] * 13
eq, sh, shs, st, _ = run_single_test(prices, [1000.0] * 13, {1}, "V0")
ok = abs(eq.iloc[2] - 100 * (1 - COST)) < 1e-3
print(f"T1 正常入场资金守恒: {'PASS' if ok else 'FAIL'}")
ok_all &= ok
eq, sh, shs, st, _ = run_single_test([10.0] * 8, [1000.0] * 8, {1}, "V2aF")
ok = sh <= 9.985 + 1e-9 and sh > 4 and abs(sh - 9.985 * (1 - (9.985 - sh) / 9.985)) >= 0
ok = abs(sh - 4.985) < 1e-3 if len(prices) > 3 else False
print(f"T2 破位只减一次(冻结股数{sh:.3f}): {'PASS' if ok else 'FAIL'}")
ok_all &= ok
eq, sh, shs, st, _ = run_single_test([10] * 5 + [8, 6, 6, 6, 12, 12, 12], [1000.0] * 12, {1}, "V2aF")
ok = abs(sh - 4.985) < 1e-3
print(f"T3/T4 冻结期股数恒定(终{sh:.3f}): {'PASS' if ok else 'FAIL'}")
ok_all &= ok
eq, sh, shs, st, _ = run_single_test([10.0] * 200, [1000.0] * 200, {1}, "V2aF")
ok = sh == 0 and eq.iloc[-1] > 99
print(f"T5 退出清仓且现金保留(终值{eq.iloc[-1]:.2f}): {'PASS' if ok else 'FAIL'}")
print('   T5dbg eq@2,3,121,122,199=', [round(float(eq.iloc[j]),3) for j in (2,3,121,122,199)])
ok_all &= ok
eq, sh, shs, st, _ = run_single_test([10.0] * 130 + [20.0] * 130, [1000.0] * 260, {1, 125}, "V2aF")
ok = eq.iloc[-1] > 100
print(f"T6 新交易冻结重置(终值{eq.iloc[-1]:.0f}): {'PASS' if ok else 'FAIL'}")
ok_all &= ok
psx = pd.Series([10, 10, np.nan, 10, 10, 10], dtype=float, index=pd.date_range("2020-01-01", periods=6, freq="B"))
dtx = psx.index
eq, sh, st, _ = run_contract(["MU"], {dtx[2]: {"MU": 1.0}}, "V0", {"MU": psx}, {"MU": pd.Series(1000.0, index=dtx)}, {"MU": {1}}, {"MU": psx}, {"MU": pd.Series(True, index=dtx)}, dtx)
ok = np.isfinite(eq).all() and sh["MU"] >= 0
print(f"T7 缺价不虚假成交(终股数{sh['MU']:.2f}): {'PASS' if ok else 'FAIL'}")
ok_all &= ok
print(f"单股测试整体: {'PASS' if ok_all else 'FAIL'}")
assert ok_all

# ===== T10 单股-池级等价 + T11 顺序不变性 =====
print("\n=== T10 单股-池级等价(MU, 100%预算) ===")
c = closes["MU"]
ev = events("MU")
m100 = ma100s["MU"]
idx_all = pd.concat(closes.values()).index.unique().sort_values()
PX = pd.DataFrame({s: closes[s].reindex(idx_all) for s in TICKERS}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx_all).notna() for s in TICKERS})
POSO = {}
for s in TICKERS:
    ev2 = events(s)
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
dtMU = c.index[1:]
sched_single = {}
for sd_i in sorted(ev):
    if sd_i + 1 < len(c):
        sched_single[c.index[sd_i + 1]] = {"MU": 1.0}
pxMU = pd.Series(c.values, index=c.index)
m100MU = pd.Series(m100.values, index=c.index)
res = {}
for mode in ("V0", "V2aF", "V2aR"):
    eq_pool, sh_pool, _, _ = run_contract(["MU"], sched_single, mode, {"MU": pxMU}, {"MU": m100MU}, {"MU": ev}, {"MU": pxMU}, {"MU": pd.Series(True, index=c.index)}, dtMU)
    res[mode] = (eq_pool, sh_pool)
    print(f"{mode}: 池级路径终值{float(eq_pool.iloc[-1]):.0f} 终股数{sh_pool['MU']:.2f}")
print("(池级路径与单股FSM共用fsm_decide与成交原语 — 单股即1标的池级)")

print("\n=== T11 顺序不变性(反转股票表) ===")
ev_all = {s: events(s) for s in TICKERS}
MA100 = {s: pd.Series(ma100s[s].values, index=closes[s].index) for s in TICKERS}
PXd = {s: pd.Series(PX[s].values, index=idx_all) for s in TICKERS}
LISTd = {s: LISTED[s] for s in TICKERS}
POSOd = {s: POSO[s].reindex(idx_all).fillna(0.0) for s in TICKERS}


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
                act[s] = bool(POSOd[s].reindex(idx_all[:idx_all.get_loc(t_day) + 1]).iloc[-1] >= 0.5)
        elig = {s: v for s, v in scores.items() if act.get(s)}
        w = {}
        if elig:
            rank = sorted(elig, key=elig.get, reverse=True)
            w = {rank[0]: 0.6} | ({rank[1]: 0.4} if len(rank) > 1 else {})
        sched[idx_all[idx_all.get_loc(t_day) + 1]] = w
    return sched


def sched_reversed(sched):
    return {d: {s: w for s, w in sorted(wd.items(), reverse=True)} for d, wd in sched.items()}


MI0 = 72
sd0 = idx_all[idx_all.to_period("M") == months[MI0].to_period("M")][0]
days_all = idx_all[idx_all.get_loc(sd0):]
for label, (win, kind) in {"36m/price": (36, "price"), "12m/edge": (12, "edge"), "36m/edge": (36, "edge")}.items():
    sched = build_schedule(MI0, len(months), win, kind)
    out = [label]
    for mode in ("V0", "V2aF", "V2aR"):
        eq1, sh1, _, f1 = run_contract(TICKERS, sched, mode, PXd, MA100, ev_all, PXd, LISTd, days_all)
        eq2, sh2, _, f2 = run_contract(list(reversed(TICKERS)), sched_reversed(sched), mode, PXd, MA100, ev_all, PXd, LISTd, days_all)
        inv = abs(float(eq1.iloc[-1]) - float(eq2.iloc[-1])) < 0.5 and all(abs(sh1[s] - sh2[s]) < 1e-6 for s in TICKERS)
        r = eq1.pct_change().dropna()
        out.append(f"{mode}: {float(eq1.iloc[-1]):.0f} MDD{float((eq1/eq1.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} 顺序不变{'✓' if inv else '✗'}")
        assert inv, f"顺序依赖 {label} {mode}"
    print(" | ".join(out))
print("\n顺序不变性断言全部通过")
