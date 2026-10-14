# -*- coding: utf-8 -*-
"""审查四十一轮: ①V2aR共享目标仓位函数(TRIM05/GROW10同受entry_shares上限) ②真逐日T10(现金/股数/NAV历史, 首差异全上下文)
③全订单缺价保护(ENTER/HALVE/TRIM05/GROW10/EXIT统一, 缺价转PENDING保留意图) ④N3→N4拒单原因分解(资金不足/已持仓/缺价/排名落选)
⑤V2aF Reserve vs Recycle消融(减仓现金隔离 vs 回池) ⑥池级矩阵重跑。"""
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
    """共享目标仓位函数: 返回('SELL', share数)或('BUY', 现金需求, 受entry_shares上限)。"""
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
    pending = {s: None for s in universe}  # PENDING订单意图(所有动作类型)
    eq = pd.Series(np.nan, index=dt_index)
    cash_h = pd.Series(np.nan, index=dt_index)
    sh_h = pd.DataFrame({s: np.nan for s in universe}, index=dt_index)
    fees = 0.0
    funnel = dict(n1=0, n2=0, n3=0, n4=0, r_cash=0, r_hold=0, r_price=0, r_rank=0)
    for k, d in enumerate(dt_index):
        if k == 0:
            eq.iloc[0], cash_h.iloc[0] = cash, cash
            continue
        sd = dt_index[k - 1]
        mkey = sd.to_period("M")
        px_today = {s: px_d[s].get(d, np.nan) for s in universe}
        px_yest = {s: px_d[s].get(sd, np.nan) for s in universe}
        orders = {}
        # 1) 昨日PENDING订单优先(缺价保护: 意图保留至可成交日)
        for s in universe:
            if pending[s] and np.isfinite(px_today[s]) and px_today[s] > 0 and listed_d[s].get(d, False):
                orders[s] = pending[s]
                pending[s] = None
            elif pending[s]:
                funnel["r_price"] += 1
        # 2) 新决策(基于昨日收盘)
        cands = []
        for s in universe:
            if s in orders or pending[s]:
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
                orders[s] = act
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
        # 3) 卖出类执行(全部先过可成交检查)
        for s, act in list(orders.items()):
            if not (np.isfinite(px_today[s]) and px_today[s] > 0 and listed_d[s].get(d, False)):
                pending[s] = act  # 缺价: 保留意图
                funnel["r_price"] += 1
                del orders[s]
                continue
            p = px_today[s]
            if act == "EXIT":
                proceeds = shares[s] * p * (1 - COST)
                fees += shares[s] * p * COST
                cash += proceeds + reserve[s]
                reserve[s] = 0.0
                shares[s], state[s], entry_shares[s] = 0.0, "FLAT", 0.0
            elif act == "HALVE":
                sell_sh = shares[s] / 2.0
                proceeds = sell_sh * p * (1 - COST)
                fees += sell_sh * p * COST
                if reserve_mode:
                    reserve[s] += proceeds  # Reserve: 减仓现金隔离至原持仓退出
                else:
                    cash += proceeds
                shares[s] -= sell_sh
                state[s] = "FROZEN"
            elif act == "V2AR_TGT":
                side, amt = v2ar_target(below := (px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False, entry_budget[s], entry_shares[s], shares[s], p)
                if side == "SELL":
                    proceeds = amt * p * (1 - COST)
                    fees += amt * p * COST
                    cash += proceeds
                    shares[s] -= amt
        nav_prev = cash + sum(shares[s] * px_yest[s] for s in universe if shares[s] > 0 and np.isfinite(px_yest[s]))
        # 4) 统一买入(ENTER + V2aR BUY), 比例分配
        buys = {}
        for s, act in orders.items():
            p = px_today[s]
            cur = shares[s] * p
            if act == "ENTER":
                buys[s] = w_map.get(s, 0.0) * nav_prev
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
            if spend <= 1e-9:
                if orders[s] == "ENTER":
                    funnel["r_cash"] += 1
                    pending[s] = "ENTER"  # 资金不足: 保留入场意图至现金可用
                continue
            fee = spend * COST
            fees += fee
            cash -= spend
            shares[s] += spend * (1 - COST) / p
            if orders[s] == "ENTER":
                funnel["n4"] += 1
                state[s], entry_i[s], entry_budget[s] = "FULL", ownpos_d[s].get(sd, -1), spend * (1 - COST)
                entry_shares[s] = shares[s]
        for s in universe:
            if state[s] != "FLAT" and shares[s] > 1e-12 and s not in orders:
                pass
        nav_now = cash + sum(shares[s] * px_today[s] for s in universe if shares[s] > 0)
        for s in universe:
            assert (state[s] == "FLAT") == (shares[s] <= 1e-12)
        assert cash >= -1e-9 and nav_now > 0
        eq.iloc[k] = nav_now
        cash_h.iloc[k] = cash
        sh_h.iloc[k] = shares[s] if len(universe) == 1 else np.nan
    if collect is not None:
        collect.update(eq=eq, cash=cash_h, sh=sh_h, funnel=funnel)
    return eq, shares, fees


ev_all = {s: events(s) for s in TICKERS}
OWNPOS = {s: pd.Series(np.arange(len(closes[s])), index=closes[s].index) for s in TICKERS}
idx_all = pd.concat(closes.values()).index.unique().sort_values()

# ===== 合成测试: V2aR股数上限 + 全订单缺价保护 =====
print("=== 合成验收测试 ===")
dts = pd.date_range("2020-01-01", periods=30, freq="B")
pxT = pd.Series([10] * 5 + [6] * 20 + [10] * 5, index=dts)  # 深跌后反弹
maT = pd.Series([1000.0] * 30, index=dts)
snapT = {(d.to_period("M"), "MU"): 1.0 for d in dts}
ownT = pd.Series(np.arange(30), index=dts)
collect = {}
eq, sh, _ = run_contract(["MU"], snapT, "V2aR", {"MU": pxT}, {"MU": maT}, {"MU": {1}}, {"MU": pd.Series(True, index=dts)}, dts, {"MU": ownT}, collect=collect)
es = 100 * (1 - COST) / 10
ok = sh["MU"] <= es + 1e-6
print(f"T-A V2aR股数上限: 入场{es:.3f} 终{sh['MU']:.3f} {'PASS' if ok else 'FAIL'}")
assert ok
# 缺价保护: EXIT信号日缺价
dts2 = pd.date_range("2020-01-01", periods=8, freq="B")
px2 = pd.Series([10, 10, np.nan, 10, 10, 10, 10, 10], index=dts2)
own2 = pd.Series(np.arange(8), index=dts2)
eq2, sh2, _ = run_contract(["MU"], {(d.to_period("M"), "MU"): 1.0 for d in dts2}, "V0", {"MU": px2}, {"MU": pd.Series(1000.0, index=dts2)}, {"MU": {1, 3}}, {"MU": pd.Series(True, index=dts2)}, dts2, {"MU": own2})
print(f"T-B 缺价订单保护: 终值{float(eq2.iloc[-1]):.2f} {'PASS' if np.isfinite(eq2).all() else 'FAIL'}")

# ===== T10 真逐日等价 =====
print("\n=== T10 逐日精确等价 ===")


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
    if len(bad) == 0:
        print(f"{mode}: 逐日现金+股数完全一致 PASS")
    else:
        j = bad[0]
        print(f"{mode}: 首差异 {dtMU[j].date()} 现金池{cash_pool[j]:.4f}/参{cash_ref[j]:.4f} 股数池{sh_pool_d[j]:.4f}/参{sh_ref[j]:.4f}")

# ===== 池级矩阵 + 漏斗分解 + Reserve/Recycle =====
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
        nm = mode
        row.append(f"{nm}: {float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} N4={f['n4']}")
        if mode == "V0":
            row.append(f"漏斗[N1={f['n1']} N2={f['n2']} N3={f['n3']} N4={f['n4']} 现金不足={f['r_cash']} 缺价={f['r_price']} 排名落选={f['r_rank']}]")
    print(" | ".join(row))
