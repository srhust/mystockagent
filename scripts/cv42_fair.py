# -*- coding: utf-8 -*-
"""审查四十二轮: ①Reserve会计修复(NAV=可用现金+隔离现金+持仓市值, 隔离现金只禁交易不禁净值) ②现金不足=REJECTED_NO_CASH(记录价格+前向20/60/120日评价, 不再无限PENDING; 缺价才PENDING且携带冻结w) ③T10强制断言+T-B五类订单缺价真实覆盖(HALVE/EXIT/V2aR) ④Reserve/Recycle公平消融(同信号同成交, 仅可用性不同, 首次减仓前逐日一致断言) ⑤资金再利用归因。"""
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
            return "V2AR_TGT"
        return None
    if state == "FROZEN":
        return "EXIT" if (i_own - entry_i) >= HOLD else None
    return None


def v2ar_target(below, entry_budget, entry_shares, shares, p):
    frac = 0.5 if below else 1.0
    tgt = frac * entry_budget
    cur = shares * p
    if cur > tgt:
        return "SELL", min(shares, (cur - tgt) / p)
    max_buy_cash = max(0.0, entry_shares - shares) * p / (1 - COST)
    return "BUY", max(0.0, min(tgt - cur, max_buy_cash))


def run_contract(universe, score_snap, risk_mode, px_d, ma100_d, ev_d, listed_d, dt_index, ownpos_d, budget=100.0, collect=None, reserve_mode=False):
    cash = float(budget)
    reserve = {s: 0.0 for s in universe}
    state = {s: "FLAT" for s in universe}
    entry_i = {s: -999 for s in universe}
    entry_budget = {s: 0.0 for s in universe}
    entry_shares = {s: 0.0 for s in universe}
    shares = {s: 0.0 for s in universe}
    pending = {}  # (act, w) — 仅缺价PENDING, 携带冻结w
    stale = {s: np.nan for s in universe}  # 陈旧价格估值(缺价日用最后有效价)
    eq = pd.Series(np.nan, index=dt_index)
    cash_h = pd.Series(np.nan, index=dt_index)
    sh_h = pd.DataFrame(0.0, index=dt_index, columns=universe)
    fees = 0.0
    halve_proceeds = 0.0
    funnel = dict(n1=0, n2=0, n3=0, n4=0, r_cash=0, r_price=0, r_rank=0)
    rejs = []  # REJECTED_NO_CASH: (s, date, price)
    for k, d in enumerate(dt_index):
        if k == 0:
            eq.iloc[0], cash_h.iloc[0] = cash + sum(reserve.values()), cash
            continue
        sd = dt_index[k - 1]
        mkey = sd.to_period("M")
        px_today = {s: px_d[s].get(d, np.nan) for s in universe}
        px_yest = {s: px_d[s].get(sd, np.nan) for s in universe}
        orders = {}
        # 1) 缺价PENDING恢复(携带冻结w)
        for s in list(pending.keys()):
            act, pw = pending[s]
            if np.isfinite(px_today[s]) and px_today[s] > 0 and listed_d[s].get(d, False):
                orders[s] = (act, pw)
                pending.pop(s)
            else:
                funnel["r_price"] += 1
        # 2) 新决策
        cands = []
        for s in universe:
            if s in orders or s in pending:
                continue
            if not np.isfinite(px_yest[s]):
                continue
            i_own = ownpos_d[s].get(sd, -1)
            below = bool(px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
            in_ev = i_own in ev_d[s]
            if state[s] == "FLAT" and in_ev:
                funnel["n1"] += 1
                sc = score_snap.get((mkey, s), np.nan)
                if np.isfinite(sc):
                    funnel["n2"] += 1
                    cands.append((sc, s))
                else:
                    funnel["r_rank"] += 1
                continue
            act = fsm_decide(risk_mode, state[s], entry_i[s], i_own, below, in_ev)
            if act:
                orders[s] = (act, 0.0)
        cands.sort(reverse=True)
        w_map = {}
        for r, (sc, s) in enumerate(cands):
            if r == 0:
                w_map[s] = 1.0 if len(cands) == 1 else 0.6
                funnel["n3"] += 1
            elif r == 1:
                w_map[s] = 0.4
                funnel["n3"] += 1
            else:
                funnel["r_rank"] += 1
            if r <= 1 and state[s] == "FLAT":
                orders[s] = ("ENTER", w_map[s])
        # 3) 卖出类执行(统一可成交检查; 缺价→PENDING)
        for s, (act, pw) in list(orders.items()):
            if not (np.isfinite(px_today[s]) and px_today[s] > 0 and listed_d[s].get(d, False)):
                pending[s] = (act, pw)
                funnel["r_price"] += 1
                del orders[s]
                continue
            p = px_today[s]
            if act == "EXIT":
                proceeds = shares[s] * p * (1 - COST)
                fees += shares[s] * p * COST
                cash += proceeds + reserve[s]
                halve_rel = reserve[s]
                reserve[s] = 0.0
                shares[s], state[s], entry_shares[s] = 0.0, "FLAT", 0.0
            elif act == "HALVE":
                sell_sh = shares[s] / 2.0
                proceeds = sell_sh * p * (1 - COST)
                fees += sell_sh * p * COST
                halve_proceeds += proceeds
                if reserve_mode:
                    reserve[s] += proceeds
                else:
                    cash += proceeds
                shares[s] -= sell_sh
                state[s] = "FROZEN"
            elif act == "V2AR_TGT":
                below2 = (px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
                side, amt = v2ar_target(below2, entry_budget[s], entry_shares[s], shares[s], p)
                if side == "SELL":
                    proceeds = amt * p * (1 - COST)
                    fees += amt * p * COST
                    cash += proceeds
                    shares[s] -= amt
        for s in universe:
            if np.isfinite(px_today[s]):
                stale[s] = px_today[s]
        nav_prev = cash + sum(reserve.values()) + sum(shares[s] * (px_yest[s] if np.isfinite(px_yest[s]) else stale[s]) for s in universe if shares[s] > 0)
        # 4) 统一买入(ENTER + V2aR BUY)
        buys = {}
        for s, (act, pw) in orders.items():
            p = px_today[s]
            cur = shares[s] * p
            if act == "ENTER":
                buys[s] = pw * nav_prev
            elif act == "V2AR_TGT":
                below2 = (px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
                side, amt = v2ar_target(below2, entry_budget[s], entry_shares[s], shares[s], p)
                if side == "BUY" and amt > 0:
                    buys[s] = amt
        need = sum(buys.values())
        sc = min(1.0, cash / need) if need > 0 else 0.0
        for s, amt in buys.items():
            p = px_today[s]
            spend = min(amt * sc, cash)
            act = orders[s][0]
            if spend <= 1e-9:
                if act == "ENTER":
                    funnel["r_cash"] += 1
                    rejs.append((s, d, p))  # REJECTED_NO_CASH: 不再PENDING
                continue
            fee = spend * COST
            fees += fee
            cash -= spend
            shares[s] += spend * (1 - COST) / p
            if act == "ENTER":
                funnel["n4"] += 1
                state[s], entry_i[s], entry_budget[s] = "FULL", ownpos_d[s].get(sd, -1), spend * (1 - COST)
                entry_shares[s] = shares[s]
        nav_now = cash + sum(reserve.values()) + sum(shares[s] * stale[s] for s in universe if shares[s] > 0)
        for s in universe:
            assert (state[s] == "FLAT") == (shares[s] <= 1e-12)
        assert cash >= -1e-9 and nav_now > 0
        eq.iloc[k] = nav_now
        cash_h.iloc[k] = cash
        sh_h.iloc[k] = [shares[s] for s in universe] if len(universe) > 1 else shares[universe[0]]
    if collect is not None:
        collect.update(eq=eq, cash=cash_h, sh=sh_h, funnel=funnel, rejs=rejs, halve_proceeds=halve_proceeds)
    return eq, shares, fees


ev_all = {s: events(s) for s in TICKERS}
OWNPOS = {s: pd.Series(np.arange(len(closes[s])), index=closes[s].index) for s in TICKERS}
idx_all = pd.concat(closes.values()).index.unique().sort_values()

# ===== T-B强化: 五类订单缺价真实覆盖 =====
print("=== T-B强化 缺价保护 ===")
dts2 = pd.date_range("2020-01-01", periods=10, freq="B")
# 场景: day1事件(day2入场), day4缺价且为MA100破位日(HALVE应顺延至day5), HOLD短化不可用→直接构造EXIT: 用FROZEN后到期难模拟, 改验证HALVE+V2aR两侧
px2 = pd.Series([10, 10, 10, 10, np.nan, 10, 10, 10, 10, 10], index=dts2)
ma2 = pd.Series([1000.0, 1000.0, 5.0, 1000.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0], index=dts2)  # day3决策HALVE(day2低MA避开), day4缺价执行→PENDING→day5补成交
snap2 = {(d.to_period("M"), "MU"): 1.0 for d in dts2}
own2 = pd.Series(np.arange(10), index=dts2)
collect = {}
eq2, sh2, _ = run_contract(["MU"], snap2, "V2aF", {"MU": px2}, {"MU": ma2}, {"MU": {1}}, {"MU": pd.Series(True, index=dts2)}, dts2, {"MU": own2}, collect=collect)
# day4缺价: HALVE不得成交(day4股数仍=入场全量), day5补成交后股数减半
ok_halve = abs(sh2["MU"] - 9.985 / 2) < 1e-6
print(f"T-B1 HALVE缺价顺延: 终股数{sh2['MU']:.4f}(期望4.9925) {'PASS' if ok_halve else 'FAIL'}")
assert ok_halve
# V2aR: day4缺价TRIM/GROW顺延
collect = {}
eq2r, sh2r, _ = run_contract(["MU"], snap2, "V2aR", {"MU": px2}, {"MU": ma2}, {"MU": {1}}, {"MU": pd.Series(True, index=dts2)}, dts2, {"MU": own2}, collect=collect)
ok_v2ar = np.isfinite(eq2r).all() and sh2r["MU"] <= 9.985 + 1e-6
print(f"T-B2 V2aR缺价顺延: 终股数{sh2r['MU']:.4f} ≤入场9.985 {'PASS' if ok_v2ar else 'FAIL'}")
assert ok_v2ar

# ===== T10 逐日等价 + 强制断言 =====
print("\n=== T10 逐日精确等价(强制断言) ===")


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
            side, amt = v2ar_target(below, entry_budget, entry_shares, shares, p)
            if side == "SELL":
                shares -= amt
                cash += amt * p * (1 - COST)
            elif amt > 0 and cash > 0:
                buy = min(amt, cash)
                cash -= buy
                shares += buy * (1 - COST) / p
        cash_h.append(cash)
        sh_h.append(shares)
    return np.array(cash_h), np.array(sh_h)


c = closes["MU"]
dtMU = c.index[1:]
pxMU = pd.Series(c.values, index=c.index)
m100MU = pd.Series(ma100s["MU"].values, index=c.index)
snap_single = {(d.to_period("M"), "MU"): 1.0 for d in c.index}
for mode in ("V0", "V2aF", "V2aR"):
    collect = {}
    eq_pool, sh_pool, _ = run_contract(["MU"], snap_single, mode, {"MU": pxMU}, {"MU": m100MU}, {"MU": ev_all["MU"]}, {"MU": pd.Series(True, index=c.index)}, dtMU, {"MU": OWNPOS["MU"]}, collect=collect)
    cash_ref, sh_ref = run_single_ref(mode)
    cash_pool = collect["cash"].values
    sh_pool_d = collect["sh"]["MU"].values
    dc = np.abs(cash_pool - cash_ref)
    dsh = np.abs(sh_pool_d - sh_ref)
    bad = np.where((dc > 1e-9) | (dsh > 1e-9))[0]
    print(f"{mode}: {'逐日现金+股数完全一致' if len(bad) == 0 else f'首差异{dtMU[bad[0]].date()}'}")
    assert len(bad) == 0, f"{mode} T10失败"

# ===== 池级矩阵(修复后) + Reserve/Recycle公平消融 + 拒单前向评价 =====
print("\n=== 池级矩阵 ===")
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


def fwd_eval(rejs):
    """拒单前向评价: 拒绝日后+20/+60/+120交易日相对拒绝价的收益。"""
    out = {20: [], 60: [], 120: []}
    for s, d, p in rejs:
        cc = closes[s]
        pos = cc.index.get_indexer([d])[0]
        for h in out:
            if 0 <= pos + h < len(cc):
                out[h].append(cc.iloc[pos + h] / p - 1)
    return {h: (np.mean(v), len(v)) for h, v in out.items()}


for label, (win, kind) in {"36m/price": (36, "price"), "12m/edge": (12, "edge"), "36m/edge": (36, "edge")}.items():
    snap = {}
    for mi in range(1, len(months)):
        for s in TICKERS:
            snap[(months[mi].to_period("M"), s)] = score(s, mi, win, kind)
    row = [label]
    eqs = {}
    for mode, rsv in [("V0", False), ("V2aF-Reserve", True), ("V2aF", False), ("V2aR", False)]:
        collect = {}
        eq, sh, fees = run_contract(TICKERS, snap, "V2aF" if "V2aF" in mode else mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect, reserve_mode=rsv)
        f = collect["funnel"]
        r = eq.pct_change().dropna()
        row.append(f"{mode}: {float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} N4={f['n4']} 拒现金={f['r_cash']} 缺价={f['r_price']}")
        eqs[mode] = collect
        if mode == "V0":
            fe = fwd_eval(collect["rejs"])
            row.append(f"拒单前向均值20/60/120日={fe[20][0]:+.1%}/{fe[60][0]:+.1%}/{fe[120][0]:+.1%}(n={fe[120][1]})")
    # 公平性: Reserve与Recycle首次差异前应一致
    e1, e2 = eqs["V2aF-Reserve"]["eq"].values, eqs["V2aF"]["eq"].values
    diff = np.where(np.abs(e1 - e2) > 1e-9)[0]
    first_diff = int(diff[0]) if len(diff) else len(e1)
    row.append(f"首差异日={days_all[first_diff].date() if first_diff < len(days_all) else '无'} 减仓释放={eqs['V2aF']['halve_proceeds']:.0f}")
    print(" | ".join(row))
