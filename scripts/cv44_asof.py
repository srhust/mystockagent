# -*- coding: utf-8 -*-
"""审查四十四轮: ①T-1估值as-of修复(先用旧stale算信号快照, 再更新今日价; T-D合成测试验证) ②资金来源账本(FIFO: INITIAL/NORMAL_EXIT/RISK_HALVE/OTHER_SELL, 入场记录真实来源构成, 支持跨日) ③真守恒恒等式(NAV_t−NAV_{t-1}=Σq_{t-1}ΔP−fee_t, 前日份额+当日费用独立核算) + fill ledger(T-C断言signal_date/fill_date) ④拒单相对收益同日历配对 ⑤评分器分歧三分类(集合/顺序/实际成交)。"""
import os
import sys
from collections import deque

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
    sources = deque([("INITIAL", cash)])  # P0-2 资金来源账本(FIFO)
    state = {s: "FLAT" for s in universe}
    entry_i = {s: -999 for s in universe}
    entry_budget = {s: 0.0 for s in universe}
    entry_shares = {s: 0.0 for s in universe}
    shares = {s: 0.0 for s in universe}
    pending = {}
    stale = {s: np.nan for s in universe}
    eq = pd.Series(np.nan, index=dt_index)
    sh_h = pd.DataFrame(0.0, index=dt_index, columns=universe)
    fees = 0.0
    halve_proceeds = 0.0
    funnel = dict(n1=0, n2=0, n3=0, n4=0, r_cash=0, r_price=0, r_rank=0, reconf=0, halve_funded_n=0, halve_funded_amt=0.0)
    rejs, fills = [], []
    for k, d in enumerate(dt_index):
        if k == 0:
            eq.iloc[0] = cash + sum(reserve.values())
            continue
        sd = dt_index[k - 1]
        mkey = sd.to_period("M")
        px_today = {s: px_d[s].get(d, np.nan) for s in universe}
        px_yest = {s: px_d[s].get(sd, np.nan) for s in universe}
        stale_prev = dict(stale)
        # P0-1: 先用旧stale计算T-1快照(不读今日价), 再更新stale
        val_sig = {s: (px_yest[s] if np.isfinite(px_yest[s]) else stale[s]) for s in universe}
        for s in universe:
            if np.isfinite(px_today[s]):
                stale[s] = px_today[s]
        nav_sig = cash + sum(reserve.values()) + sum(shares[s] * val_sig[s] for s in universe if shares[s] > 0)
        nav_prev = cash + sum(reserve.values()) + sum(shares[s] * stale_prev[s] for s in universe if shares[s] > 0)
        shares_prev = dict(shares)
        reserve_prev = sum(reserve.values())
        day_fee = 0.0
        orders = {}
        for s in list(pending.keys()):
            act, pw, sig_d = pending[s]
            if np.isfinite(px_today[s]) and px_today[s] > 0 and listed_d[s].get(d, False):
                if act == "V2AR_TGT":
                    below_now = bool(px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
                    new_act = fsm_decide(risk_mode, state[s], entry_i[s], ownpos_d[s].get(sd, -1), below_now, ownpos_d[s].get(sd, -1) in ev_d[s])
                    if new_act != act:
                        funnel["reconf"] += 1
                    act = new_act if new_act else act
                orders[s] = (act, pw, sig_d)
                pending.pop(s)
            else:
                funnel["r_price"] += 1
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
                orders[s] = (act, 0.0, sd)
        cands.sort(reverse=True)
        for r, (sc, s) in enumerate(cands):
            if r <= 1:
                funnel["n3"] += 1
            else:
                funnel["r_rank"] += 1
            if r <= 1 and state[s] == "FLAT":
                orders[s] = ("ENTER", (1.0 if len(cands) == 1 else (0.6 if r == 0 else 0.4)), sd)
        for s, (act, pw, sig_d) in list(orders.items()):
            if not (np.isfinite(px_today[s]) and px_today[s] > 0 and listed_d[s].get(d, False)):
                pending[s] = (act, pw, sig_d)
                funnel["r_price"] += 1
                del orders[s]
                continue
            p = px_today[s]
            if act == "EXIT":
                q = shares[s]
                proceeds = q * p * (1 - COST)
                fee = q * p * COST
                fees += fee
                day_fee += fee
                cash += proceeds + reserve[s]
                reserve[s] = 0.0
                sources.append(("NORMAL_EXIT", proceeds))
                shares[s], state[s], entry_shares[s] = 0.0, "FLAT", 0.0
                fills.append((s, sig_d, d, "EXIT", q, p, fee))
            elif act == "HALVE":
                q = shares[s] / 2.0
                proceeds = q * p * (1 - COST)
                fee = q * p * COST
                fees += fee
                day_fee += fee
                halve_proceeds += proceeds
                if reserve_mode:
                    reserve[s] += proceeds
                else:
                    cash += proceeds
                    sources.append(("RISK_HALVE", proceeds))
                shares[s] -= q
                state[s] = "FROZEN"
                fills.append((s, sig_d, d, "HALVE", q, p, fee))
            elif act == "V2AR_TGT":
                below2 = bool(px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
                side, amt = v2ar_target(below2, entry_budget[s], entry_shares[s], shares[s], p)
                if side == "SELL":
                    q = amt
                    proceeds = q * p * (1 - COST)
                    fee = q * p * COST
                    fees += fee
                    day_fee += fee
                    cash += proceeds
                    sources.append(("OTHER_SELL", proceeds))
                    shares[s] -= q
                    fills.append((s, sig_d, d, "V2AR_SELL", q, p, fee))
        buys = {}
        for s, (act, pw, sig_d) in orders.items():
            p = px_today[s]
            if act == "ENTER":
                buys[s] = (pw * nav_sig, pw, sig_d)
            elif act == "V2AR_TGT":
                below2 = bool(px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
                side, amt = v2ar_target(below2, entry_budget[s], entry_shares[s], shares[s], p)
                if side == "BUY" and amt > 0:
                    buys[s] = (amt, 0.0, sig_d)
        need = sum(v[0] for v in buys.values())
        sc = min(1.0, cash / need) if need > 0 else 0.0
        for s, (amt, pw, sig_d) in buys.items():
            p = px_today[s]
            spend = min(amt * sc, cash)
            act = orders[s][0]
            if spend <= 1e-9:
                if act == "ENTER":
                    funnel["r_cash"] += 1
                    rejs.append((s, d, p))
                continue
            fee = spend * COST
            fees += fee
            day_fee += fee
            cash -= spend
            # FIFO来源消耗 + 构成记录
            comp = {}
            left = spend
            while left > 1e-9 and sources:
                lab, amt_s = sources[0]
                take = min(amt_s, left)
                comp[lab] = comp.get(lab, 0.0) + take
                if amt_s - take <= 1e-9:
                    sources.popleft()
                else:
                    sources[0] = (lab, amt_s - take)
                left -= take
            shares[s] += spend * (1 - COST) / p
            if act == "ENTER":
                funnel["n4"] += 1
                state[s], entry_i[s], entry_budget[s] = "FULL", ownpos_d[s].get(sd, -1), spend * (1 - COST)
                entry_shares[s] = shares[s]
                fills.append((s, sig_d, d, "ENTER", shares[s] - (shares[s] - spend * (1 - COST) / p), p, fee))
                if comp.get("RISK_HALVE", 0.0) > 0.01:
                    funnel["halve_funded_n"] += 1
                    funnel["halve_funded_amt"] += comp["RISK_HALVE"]
        nav_now = cash + sum(reserve.values()) + sum(shares[s] * stale[s] for s in universe if shares[s] > 0)
        for s in universe:
            assert (state[s] == "FLAT") == (shares[s] <= 1e-12)
        assert cash >= -1e-9 and nav_now > 0
        # P0-3 真守恒恒等式: ΔNAV = Σ q_{t-1}ΔP − fee_t (+reserve变动净零)
        dnav = nav_now - nav_prev
        mkt = sum(shares_prev[s] * (stale[s] - stale_prev[s]) for s in universe if shares_prev[s] > 0)
        assert abs(dnav - (mkt - day_fee)) < 0.02, f"守恒失败 {d:.date()} {dnav:.4f} vs {mkt - day_fee:.4f}"
        eq.iloc[k] = nav_now
        sh_h.iloc[k] = [shares[s] for s in universe]
    if collect is not None:
        collect.update(eq=eq, sh=sh_h, funnel=funnel, rejs=rejs, halve_proceeds=halve_proceeds, fills=fills)
    return eq, shares, fees


ev_all = {s: events(s) for s in TICKERS}
OWNPOS = {s: pd.Series(np.arange(len(closes[s])), index=closes[s].index) for s in TICKERS}
idx_all = pd.concat(closes.values()).index.unique().sort_values()

# ===== T-D: as-of 回退测试(昨日缺价今日恢复, 快照不得读今日价) =====
print("=== T-D 信号快照as-of ===")
dts4 = pd.date_range("2022-01-03", periods=8, freq="B")
pxA = pd.Series([10, 10, np.nan, 20, 20, 20, 20, 20], index=dts4)  # A: day2缺价, day3恢复20
maA = pd.Series([1000.0, 1000.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0], index=dts4)  # day2决策HALVE(缺价→PENDING)→day3补成交释放现金
pxB = pd.Series(10.0, index=dts4)
pxD = pd.Series(10.0, index=dts4)
maB = pd.Series(1000.0, index=dts4)
snap4 = {(d.to_period("M"), s): 1.0 for d in dts4 for s in ("A", "B", "D")}
own4 = {s: pd.Series(np.arange(8), index=dts4) for s in ("A", "B", "D")}
collect = {}
eq4, sh4, _ = run_contract(["A", "B", "D"], snap4, "V2aF", {"A": pxA, "B": pxB, "D": pxD}, {"A": maA, "B": maB, "D": maB}, {"A": {0}, "B": {2}, "D": {2}}, {"A": pd.Series(True, index=dts4), "B": pd.Series(True, index=dts4), "D": pd.Series(True, index=dts4)}, dts4, own4, collect=collect)
# day1: A入场@10(9.985股); day2: HALVE决策落缺价日→PENDING; day3: HALVE补成交释放~99.7现金
# 同日B/D双候选: nav_sig须用A旧stale=10 → nav_sig=99.85 → B预算0.6×99.85→股数≈5.97
# 若as-of污染(stale先更新为20): nav_sig=199.7 → B股数≈9.96
fillB = [f for f in collect["fills"] if f[0] == "B" and f[3] == "ENTER"]
qB = fillB[0][4] if fillB else 0
ok = 3.9 < qB < 4.05
print(f"T-D: B成交股数{qB:.3f}(as-of正确≈3.98[B拿0.4档], 污染≈7.98) {'PASS' if ok else 'FAIL'}")
assert ok

# ===== T-C: EXIT成交日期严格断言 =====
print("\n=== T-C EXIT成交日期 ===")
n = 130
dts3 = pd.date_range("2021-01-04", periods=n, freq="B")
px3 = pd.Series(10.0, index=dts3)
px3.iloc[122] = np.nan
ma3 = pd.Series(1000.0, index=dts3)
snap3 = {(d.to_period("M"), "MU"): 1.0 for d in dts3}
collect = {}
eq3, sh3, _ = run_contract(["MU"], snap3, "V0", {"MU": px3}, {"MU": ma3}, {"MU": {1}}, {"MU": pd.Series(True, index=dts3)}, dts3, {"MU": pd.Series(np.arange(n), index=dts3)}, collect=collect)
ex = [f for f in collect["fills"] if f[3] == "EXIT"]
ok = (len(ex) == 1 and ex[0][1] == dts3[121] and ex[0][2] == dts3[123] and collect["sh"]["MU"].loc[dts3[122]] > 0 and collect["sh"]["MU"].loc[dts3[123]] == 0 and abs(eq3.iloc[-1] - 99.70) < 0.01)
print(f"T-C: signal={ex[0][1].date() if ex else '?'} fill={ex[0][2].date() if ex else '?'} day122持股>0 day123=0 终值{float(eq3.iloc[-1]):.2f} {'PASS' if ok else 'FAIL'}")
assert ok

# ===== 池级矩阵 + 资金来源归因 + 配对相对收益 + 分歧三分类 =====
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


def paired_rel(rejs, eq_pool):
    """同联合日历配对: 被拒股(PX前向填充)与池NAV同起止日。"""
    diffs = []
    for s, d, p in rejs:
        jpos = eq_pool.index.get_indexer([d])[0]
        if jpos + 120 >= len(eq_pool):
            continue
        pr = PX[s].iloc[jpos + 120] / p - 1
        po = eq_pool.iloc[jpos + 120] / eq_pool.iloc[jpos] - 1
        diffs.append(pr - po)
    v = np.array(diffs)
    v = v[np.isfinite(v)]
    return np.mean(v), np.median(v), float((v > 0).mean()), len(v)


snaps = {}
for label, (win, kind) in {"36m/price": (36, "price"), "12m/edge": (12, "edge"), "36m/edge": (36, "edge")}.items():
    snap = {}
    for mi in range(1, len(months)):
        for s in TICKERS:
            snap[(months[mi].to_period("M"), s)] = score(s, mi, win, kind)
    snaps[label] = snap
    row = [label]
    for mode, rsv in [("V0", False), ("V2aF-Reserve", True), ("V2aF", False), ("V2aR", False)]:
        collect = {}
        eq, sh, fees = run_contract(TICKERS, snap, "V2aF" if "V2aF" in mode else mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect, reserve_mode=rsv)
        f = collect["funnel"]
        r = eq.pct_change().dropna()
        row.append(f"{mode}: {float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} N4={f['n4']} HALVE资助={f['halve_funded_n']}笔/{f['halve_funded_amt']:.0f} 重确认={f['reconf']}")
        if mode == "V0":
            m, md, wr, n_ = paired_rel(collect["rejs"], eq)
            row.append(f"配对超额120日均{m:+.1%} 中位{md:+.1%} 胜率{wr:.0%}(n={n_})")
    print(" | ".join(row))

# ===== 评分器分歧三分类 =====
print("\n=== 评分器分歧三分类 ===")
set_diff = order_diff = fill_diff = 0
entry_sets = {}
for label, snap in snaps.items():
    collect = {}
    run_contract(TICKERS, snap, "V0", PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect)
    entry_sets[label] = set((f[0], f[2]) for f in collect["fills"] if f[3] == "ENTER")
for k in range(1, len(days_all)):
    sd = days_all[k - 1]
    mkey = sd.to_period("M")
    ev_today = [s for s in TICKERS if OWNPOS[s].get(sd, -1) in ev_all[s]]
    if len(ev_today) < 2:
        continue
    picks = {}
    for label, snap in snaps.items():
        cands = sorted([(snap.get((mkey, s), np.nan), s) for s in ev_today], reverse=True)
        cands = [(sc, s) for sc, s in cands if np.isfinite(sc)]
        picks[label] = (frozenset(s for _, s in cands[:2]), tuple(s for _, s in cands[:2]))
    sets = {p[0] for p in picks.values()}
    if len(sets) > 1:
        set_diff += 1
    elif len({p[1] for p in picks.values()}) > 1:
        order_diff += 1
f0 = entry_sets["36m/price"]
fill_diff = len(entry_sets["12m/edge"] ^ f0) + len(entry_sets["36m/edge"] ^ f0)
print(f"多候选日: TOP2集合分歧={set_diff} 仅顺序分歧={order_diff} 三评分器实际入场集合差异(与36m/price对称差)={fill_diff}")
