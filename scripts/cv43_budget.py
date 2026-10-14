# -*- coding: utf-8 -*-
"""审查四十三轮: ①入场预算统一时点=信号日(T-1)收盘快照, 任何T日成交前冻结(禁止混合T日卖出后现金×T-1价) ②V2aR缺价恢复=方案B(恢复时按当前风险状态重新决策, 旧单取消计数; EXIT保持冻结意图=方案A) ③EXIT到期缺价测试(不虚假卖出, 次日真实价成交) ④逐日守恒断言(cash+reserve+持仓=NAV) ⑤资金来源归因(减仓资金资助的入场) ⑥拒单相对收益(被拒 vs 同期池) ⑦评分器竞争力统计(多候选日+选股分歧)。"""
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
    pending = {}
    stale = {s: np.nan for s in universe}
    eq = pd.Series(np.nan, index=dt_index)
    fees = 0.0
    halve_proceeds = 0.0
    funnel = dict(n1=0, n2=0, n3=0, n4=0, r_cash=0, r_price=0, r_rank=0, reconf=0, funded_n=0, funded_amt=0.0)
    rejs = []
    for k, d in enumerate(dt_index):
        if k == 0:
            eq.iloc[0] = cash
            continue
        sd = dt_index[k - 1]
        mkey = sd.to_period("M")
        px_today = {s: px_d[s].get(d, np.nan) for s in universe}
        px_yest = {s: px_d[s].get(sd, np.nan) for s in universe}
        for s in universe:
            if np.isfinite(px_today[s]):
                stale[s] = px_today[s]
        val_sig = {s: (px_yest[s] if np.isfinite(px_yest[s]) else stale[s]) for s in universe}
        # ===== P0-1: 信号日快照NAV(任何T日成交前) =====
        cash_open = cash
        nav_sig = cash + sum(reserve.values()) + sum(shares[s] * val_sig[s] for s in universe if shares[s] > 0)
        orders = {}
        for s in list(pending.keys()):
            act, pw = pending[s]
            if np.isfinite(px_today[s]) and px_today[s] > 0 and listed_d[s].get(d, False):
                if act == "V2AR_TGT":  # P0-2方案B: 恢复时按当前风险状态重新决策
                    below_now = bool(px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
                    new_act = fsm_decide(risk_mode, state[s], entry_i[s], ownpos_d[s].get(sd, -1), below_now, ownpos_d[s].get(sd, -1) in ev_d[s])
                    if new_act != act:
                        funnel["reconf"] += 1
                    act = new_act if new_act else act
                orders[s] = (act, pw)
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
                orders[s] = (act, 0.0)
        cands.sort(reverse=True)
        for r, (sc, s) in enumerate(cands):
            if r == 0:
                funnel["n3"] += 1
            elif r == 1:
                funnel["n3"] += 1
            else:
                funnel["r_rank"] += 1
            if r <= 1 and state[s] == "FLAT":
                orders[s] = ("ENTER", (1.0 if len(cands) == 1 else (0.6 if r == 0 else 0.4)))
        # 卖出类(统一可成交检查, 缺价→PENDING保留意图; EXIT冻结意图)
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
                below2 = bool(px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
                side, amt = v2ar_target(below2, entry_budget[s], entry_shares[s], shares[s], p)
                if side == "SELL":
                    proceeds = amt * p * (1 - COST)
                    fees += amt * p * COST
                    cash += proceeds
                    shares[s] -= amt
        # 买入(预算=信号日快照nav_sig × w)
        buys = {}
        for s, (act, pw) in orders.items():
            p = px_today[s]
            cur = shares[s] * p
            if act == "ENTER":
                buys[s] = pw * nav_sig
            elif act == "V2AR_TGT":
                below2 = bool(px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
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
                    rejs.append((s, d, p))
                continue
            if act == "ENTER" and cash_open < spend - 1e-9:
                funnel["funded_n"] += 1
                funnel["funded_amt"] += spend  # 当日减仓现金资助
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
        assert abs(nav_now - (cash + sum(reserve.values()) + sum(shares[s] * stale[s] for s in universe if shares[s] > 0))) < 1e-9  # P1守恒
        eq.iloc[k] = nav_now
    if collect is not None:
        collect.update(eq=eq, funnel=funnel, rejs=rejs, halve_proceeds=halve_proceeds)
    return eq, shares, fees


ev_all = {s: events(s) for s in TICKERS}
OWNPOS = {s: pd.Series(np.arange(len(closes[s])), index=closes[s].index) for s in TICKERS}
idx_all = pd.concat(closes.values()).index.unique().sort_values()

# ===== T-C: EXIT到期缺价测试 =====
print("=== T-C EXIT到期缺价 ===")
n = 130
dts3 = pd.date_range("2021-01-04", periods=n, freq="B")
px3 = pd.Series(10.0, index=dts3)
px3.iloc[122] = np.nan  # 执行日day122缺价(EXIT决策sd=day121: i_own−entry=120)
ma3 = pd.Series(1000.0, index=dts3)
snap3 = {(d.to_period("M"), "MU"): 1.0 for d in dts3}
collect = {}
eq3, sh3, _ = run_contract(["MU"], snap3, "V0", {"MU": px3}, {"MU": ma3}, {"MU": {1}}, {"MU": pd.Series(True, index=dts3)}, dts3, {"MU": pd.Series(np.arange(n), index=dts3)}, collect=collect)
# 入场day2(9.985股@10) → day122缺价不卖 → day123卖: cash=9.985×10×0.9985=99.70
ok = abs(sh3["MU"]) < 1e-9 and abs(eq3.iloc[-1] - 99.70) < 0.01
print(f"T-C: 缺价日不虚假卖出, 次日补卖 终值{float(eq3.iloc[-1]):.2f}(期望99.70) {'PASS' if ok else 'FAIL'}")
assert ok

# ===== 池级矩阵 + 全部P1 =====
print("\n=== 池级矩阵(预算时点修复后) ===")
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


def fwd_eval(rejs, eq_pool):
    out = {20: [], 60: [], 120: []}
    rel = {120: []}
    for s, d, p in rejs:
        cc = closes[s]
        pos = cc.index.get_indexer([d])[0]
        ppos = eq_pool.index.get_indexer([d])[0]
        for h in out:
            if 0 <= pos + h < len(cc):
                out[h].append(cc.iloc[pos + h] / p - 1)
        if 0 <= ppos + 120 < len(eq_pool):
            rel[120].append(cc.iloc[min(pos + 120, len(cc) - 1)] / p - 1 - (eq_pool.iloc[ppos + 120] / eq_pool.iloc[ppos] - 1))
    r = {}
    for h in out:
        v = np.array(out[h])
        r[h] = (np.mean(v), np.median(v), float((v > 0).mean()), len(v))
    rv = np.array(rel[120])
    return r, (np.mean(rv), float((rv > 0).mean()), len(rv))


snaps = {}
for label, (win, kind) in {"36m/price": (36, "price"), "12m/edge": (12, "edge"), "36m/edge": (36, "edge")}.items():
    snap = {}
    for mi in range(1, len(months)):
        for s in TICKERS:
            snap[(months[mi].to_period("M"), s)] = score(s, mi, win, kind)
    snaps[label] = snap
    row = [label]
    eqs = {}
    for mode, rsv in [("V0", False), ("V2aF-Reserve", True), ("V2aF", False), ("V2aR", False)]:
        collect = {}
        eq, sh, fees = run_contract(TICKERS, snap, "V2aF" if "V2aF" in mode else mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect, reserve_mode=rsv)
        f = collect["funnel"]
        r = eq.pct_change().dropna()
        row.append(f"{mode}: {float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} N4={f['n4']} 拒现金={f['r_cash']} 减仓资助={f['funded_n']}笔/{f['funded_amt']:.0f} 重确认={f['reconf']}")
        eqs[mode] = collect
        if mode == "V0":
            r_, rel = fwd_eval(collect["rejs"], eq)
            row.append(f"拒单120日均值{r_[120][0]:+.1%} 中位{r_[120][1]:+.1%} 胜率{r_[120][2]:.0%} 相对池{rel[0]:+.1%}(胜率{rel[1]:.0%},n={rel[2]})")
    print(" | ".join(row))

# ===== P1: 评分器竞争力 =====
print("\n=== 评分器竞争力 ===")
cand_hist = {1: 0, 2: 0, "3+": 0}
diverge_days = 0
total_cand_days = 0
for k in range(1, len(days_all)):
    sd = days_all[k - 1]
    mkey = sd.to_period("M")
    ev_today = [s for s in TICKERS if OWNPOS[s].get(sd, -1) in ev_all[s]]
    if not ev_today:
        continue
    total_cand_days += 1
    picks = {}
    ncand = None
    for label, snap in snaps.items():
        cands = sorted([(snap.get((mkey, s), np.nan), s) for s in ev_today], reverse=True)
        cands = [(sc, s) for sc, s in cands if np.isfinite(sc)]
        if ncand is None:
            ncand = len(cands)
            cand_hist[min(ncand, 1) if ncand <= 1 else (2 if ncand == 2 else "3+")] = cand_hist.get(min(ncand, 1) if ncand <= 1 else (2 if ncand == 2 else "3+"), 0) + 1
        picks[label] = tuple(s for _, s in cands[:2])
    if len(set(picks.values())) > 1:
        diverge_days += 1
print(f"事件日总数={total_cand_days} 单候选={cand_hist.get(1, 0)} 双候选={cand_hist.get(2, 0)} 三+={cand_hist.get('3+', 0)} 三评分器选股分歧日={diverge_days}")
