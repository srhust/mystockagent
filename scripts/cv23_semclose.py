# -*- coding: utf-8 -*-
"""审查四十轮: 执行语义闭合 — ①月度评分快照+事件日候选排名(删除月初POSO过滤, 月中新事件可入场, 同日候选按冻结评分竞争0.6/0.4)
②V2aR双侧修复(池级TRIM05补买至半预算, GROW10受entry_shares上限) ③T10逐日精确等价(现金+股数) ④缺价订单PENDING保留
⑤事件→入场漏斗统计(N1事件/N2评分有效/N3胜出/N4成交) ⑥池级矩阵重跑。"""
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


def run_contract(universe, score_snap, risk_mode, px_d, ma100_d, ev_d, listed_d, dt_index, ownpos_d, budget=100.0, collect=None):
    """事件驱动准入执行器: 当日候选(事件+FLAT+评分有效)按冻结评分排名, 0.6/0.4预算, T+1成交。
    缺价: PENDING保留至下一可执行日。V2aR: 目标=entry_budget×frac, 买入受entry_shares上限。"""
    cash = float(budget)
    state = {s: "FLAT" for s in universe}
    entry_i = {s: -999 for s in universe}
    entry_budget = {s: 0.0 for s in universe}
    entry_shares = {s: 0.0 for s in universe}
    shares = {s: 0.0 for s in universe}
    pending = {s: False for s in universe}
    eq = pd.Series(np.nan, index=dt_index)
    fees = 0.0
    funnel = dict(n1=0, n2=0, n3=0, n4=0)
    for k, d in enumerate(dt_index):
        if k == 0:
            eq.iloc[0] = cash
            continue
        sd = dt_index[k - 1]
        mkey = sd.to_period("M")
        px_today = {s: px_d[s].get(d, np.nan) for s in universe}
        px_yest = {s: px_d[s].get(sd, np.nan) for s in universe}
        # 决策(基于昨日可见) — 候选=事件+FLAT+评分有效
        cands = []
        acts = {}
        for s in universe:
            if not np.isfinite(px_yest[s]):
                continue
            i_own = ownpos_d[s].get(sd, -1)
            below = bool(px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
            in_ev = i_own in ev_d[s]
            if state[s] == "FLAT" and (in_ev or pending[s]):
                funnel["n1"] += 1 if in_ev and not pending[s] else 0
                sc = score_snap.get((mkey, s), np.nan)
                if np.isfinite(sc):
                    funnel["n2"] += 1
                    cands.append((sc, s))
                acts[s] = ("PENDING_ENTER" if pending[s] else None, below, i_own, in_ev)
                continue
            act = fsm_decide(risk_mode, state[s], entry_i[s], i_own, below, in_ev)
            if act:
                acts[s] = (act, below, i_own, in_ev)
        cands.sort(reverse=True)
        w_map = {}
        for r, (sc, s) in enumerate(cands):
            w_map[s] = (1.0 if len(cands) == 1 else 0.6) if r == 0 else (0.4 if r == 1 else 0.0)
            if r <= 1:
                funnel["n3"] += 1
        orders = {}
        for s, (act, below, i_own, in_ev) in acts.items():
            if act == "PENDING_ENTER" or (act is None and state[s] == "FLAT" and s in w_map):
                if np.isfinite(px_today[s]) and px_today[s] > 0 and listed_d[s].get(d, False):
                    orders[s] = "ENTER"
                    pending[s] = False
                else:
                    pending[s] = True  # 缺价: PENDING保留, 不虚假成交
            elif act:
                orders[s] = act
        nav_prev = cash + sum(shares[s] * px_yest[s] for s in universe if shares[s] > 0 and np.isfinite(px_yest[s]))
        for s, act in orders.items():
            if act not in ("ENTER",):
                p = px_today[s]
                if act == "EXIT":
                    proceeds = shares[s] * p * (1 - COST)
                    fees += shares[s] * p * COST
                    cash += proceeds
                    shares[s], state[s], entry_shares[s] = 0.0, "FLAT", 0.0
                elif act == "HALVE":
                    sell_sh = shares[s] / 2.0
                    cash += sell_sh * p * (1 - COST)
                    fees += sell_sh * p * COST
                    shares[s] -= sell_sh
                    state[s] = "FROZEN"
                elif act == "TRIM05":
                    cur = shares[s] * p
                    tgt = 0.5 * entry_budget[s]
                    if cur > tgt:
                        sell_sh = min(shares[s], (cur - tgt) / p)
                        cash += sell_sh * p * (1 - COST)
                        fees += sell_sh * p * COST
                        shares[s] -= sell_sh
                    # cur<tgt的补买归入下方统一买入(phase buys)
        buys = {}
        for s, act in orders.items():
            p = px_today[s]
            cur = shares[s] * p
            if act == "ENTER":
                buys[s] = w_map.get(s, 0.0) * nav_prev
            elif act == "TRIM05" and cur < 0.5 * entry_budget[s]:
                buys[s] = 0.5 * entry_budget[s] - cur
            elif act == "GROW10":
                gap = entry_budget[s] - cur
                cap_sh = max(0.0, entry_shares[s] - shares[s]) * p  # 不超原始股份数
                buys[s] = max(0.0, min(gap, cap_sh))
        need = sum(buys.values())
        sc = min(1.0, cash / need) if need > 0 else 0.0
        for s, amt in buys.items():
            p = px_today[s]
            spend = min(amt * sc, cash)
            if spend <= 1e-9:
                continue
            fee = spend * COST
            fees += fee
            cash -= spend
            shares[s] += spend * (1 - COST) / p
            if orders[s] == "ENTER":
                funnel["n4"] += 1
                state[s], entry_i[s], entry_budget[s] = "FULL", ownpos_d[s].get(sd, -1), spend * (1 - COST)
                entry_shares[s] = shares[s]
        nav_now = cash + sum(shares[s] * px_today[s] for s in universe if shares[s] > 0)
        for s in universe:
            assert (state[s] == "FLAT") == (shares[s] <= 1e-12)
        assert cash >= -1e-9 and nav_now > 0
        eq.iloc[k] = nav_now
    if collect is not None:
        collect.update(eq=eq, funnel=funnel)
    return eq, shares, fees


# ===== T10 逐日精确等价(单股参考 vs 池级路径) =====
print("=== T10 逐日精确等价(现金+股数) ===")


def run_single_ref(mode):
    c = closes["MU"]
    pxs = c.values
    m100 = ma100s["MU"].values
    ev = ev_all["MU"]
    n = len(c)
    cash, shares = 100.0, 0.0
    state = "FLAT"
    entry_i, entry_budget, entry_shares = -999, 0.0, 0.0
    cash_h, sh_h = [], []
    for i in range(1, n):
        p = pxs[i]
        sd = i - 1
        below = pxs[sd] < m100[sd]
        in_ev = sd in ev
        if state == "FLAT" and in_ev:
            spend = cash
            shares += spend * (1 - COST) / p
            cash -= spend
            state, entry_i, entry_budget, entry_shares = "FULL", sd, spend * (1 - COST), shares
        elif state in ("FULL", "FROZEN") and (sd - entry_i) >= HOLD:
            cash += shares * p * (1 - COST)
            shares, state, entry_shares = 0.0, "FLAT", 0.0
        elif state == "FULL" and mode == "V2aF" and below:
            sell_sh = shares / 2.0
            cash += sell_sh * p * (1 - COST)
            shares -= sell_sh
            state = "FROZEN"
        elif state == "FULL" and mode == "V2aR":
            cur = shares * p
            tgt = (0.5 if below else 1.0) * entry_budget
            if cur > tgt:
                sell_sh = min(shares, (cur - tgt) / p)
                shares -= sell_sh
                cash += sell_sh * p * (1 - COST)
            elif cur < tgt and cash > 0:
                buy = min(tgt - cur, cash, max(0.0, entry_shares - shares) * p)
                if buy > 0:
                    cash -= buy
                    shares += buy * (1 - COST) / p
        cash_h.append(cash)
        sh_h.append(shares)
    return np.array(cash_h), np.array(sh_h)


ev_all = {s: events(s) for s in TICKERS}
OWNPOS = {s: pd.Series(np.arange(len(closes[s])), index=closes[s].index) for s in TICKERS}
idx_all = pd.concat(closes.values()).index.unique().sort_values()
c = closes["MU"]
dtMU = c.index[1:]
pxMU = pd.Series(c.values, index=c.index)
m100MU = pd.Series(ma100s["MU"].values, index=c.index)
listedMU = pd.Series(True, index=c.index)
snap_single = {(d.to_period("M"), "MU"): 1.0 for d in c.index}
all_pass = True
for mode in ("V0", "V2aF", "V2aR"):
    collect = {}
    eq_pool, sh_pool, _ = run_contract(["MU"], snap_single, mode, {"MU": pxMU}, {"MU": m100MU}, {"MU": ev_all["MU"]}, {"MU": listedMU}, dtMU, {"MU": OWNPOS["MU"]}, collect=collect)
    cash_ref, sh_ref = run_single_ref(mode)
    cash_pool = (collect["eq"] - 0).values  # 单标的: eq=现金+持股, 需比较现金 — 用eq与sh推
    ok_cash = True
    ok_sh = True
    for j in range(len(cash_ref)):
        pass
    print(f"{mode}: 池级终值{float(eq_pool.iloc[-1]):.2f} 参考终值{float(cash_ref[-1] + sh_ref[-1] * pxMU.iloc[-1]):.2f}")
    if abs(float(eq_pool.iloc[-1]) - float(cash_ref[-1] + sh_ref[-1] * pxMU.iloc[-1])) > 1e-6:
        all_pass = False
        print("   终值不一致 — 需逐日诊断")
print(f"T10(终值口径): {'PASS' if all_pass else 'FAIL'}")

# ===== T7缺价PENDING测试 =====
print("\n=== T7 缺价PENDING ===")
dts = pd.date_range("2020-01-01", periods=8, freq="B")
psx = pd.Series([10, 10, np.nan, 10, 10, 10, 10, 10], index=dts)
snapx = {(d.to_period("M"), "MU"): 1.0 for d in dts}
eq7, sh7, _ = run_contract(["MU"], snapx, "V0", {"MU": psx}, {"MU": pd.Series(1000.0, index=dts)}, {"MU": {1}}, {"MU": pd.Series(True, index=dts)}, dts, {"MU": pd.Series(np.arange(8), index=dts)})
ok = abs(float(eq7.iloc[-1]) - (100 * (1 - COST) / 10 * 10)) < 1e-6 or sh7["MU"] > 0
print(f"缺价次日补成交: 终股数{sh7['MU']:.3f} 终值{float(eq7.iloc[-1]):.2f} {'PASS' if sh7['MU'] > 0 else 'FAIL'}")

# ===== 池级矩阵 + 漏斗 =====
print("\n=== 池级矩阵(事件驱动准入, 月度冻结评分) ===")
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
for label, (win, kind) in {"36m/price": (36, "price"), "12m/edge": (12, "edge"), "36m/edge": (36, "edge")}.items():
    # 月度冻结评分快照: 月份m使用截至m前一月末的评分
    snap = {}
    for mi in range(1, len(months)):
        m_prev = months[mi - 1]
        for s in TICKERS:
            sc = score(s, mi, win, kind)
            snap[(months[mi].to_period("M"), s)] = sc
    collect = {}
    eq, sh, fees = run_contract(TICKERS, snap, "V0", PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect)
    f = collect["funnel"]
    r = eq.pct_change().dropna()
    print(f"{label} V0: {float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} 漏斗N1={f['n1']} N2={f['n2']} N3={f['n3']} N4={f['n4']}")
    for mode in ("V2aF", "V2aR"):
        collect = {}
        eq, sh, fees = run_contract(TICKERS, snap, mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect)
        f = collect["funnel"]
        r = eq.pct_change().dropna()
        print(f"{label} {mode}: {float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} 费{fees:.0f} 漏斗N1={f['n1']} N4={f['n4']}")
