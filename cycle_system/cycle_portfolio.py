# -*- coding: utf-8 -*-
"""CycleTrend 正式组合执行模块(审查四十五轮迁移): 统一FSM决策(fsm_decide) + 风险目标仓位(v2ar_target) + 事件驱动准入执行器(run_contract)。
冻结契约: T+1成交, 信号日(T-1)快照预算(as-of安全), buy-once(交易级预算/原始股份上限), 缺价PENDING(V2aR恢复时重确认), FIFO资金来源账本(含RESERVE_RELEASE), 独立守恒恒等式与来源守恒断言, fill ledger。
冻结基准: 单股MU V0=49428.38, V2aF=15170.70 (budget=100, COST=0.0015, HOLD=120)。"""
import numpy as np
import pandas as pd
from collections import deque

COST = 0.0015
HOLD, DEDUP = 120, 60


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
                sources.append(("NORMAL_EXIT", proceeds))
                if reserve[s] > 1e-9:
                    sources.append(("RESERVE_RELEASE", reserve[s]))
                reserve[s] = 0.0
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
        assert abs(sum(a for _, a in sources) - cash) < 1e-8 * max(1.0, abs(cash)), f"来源账本不守恒 {d}"
        eq.iloc[k] = nav_now
        sh_h.iloc[k] = [shares[s] for s in universe]
    if collect is not None:
        collect.update(eq=eq, sh=sh_h, funnel=funnel, rejs=rejs, halve_proceeds=halve_proceeds, fills=fills)
    return eq, shares, fees


