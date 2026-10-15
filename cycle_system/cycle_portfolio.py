# -*- coding: utf-8 -*-
"""CycleTrend 正式组合执行模块(审查四十五轮迁移): 统一FSM决策(fsm_decide) + 风险目标仓位(v2ar_target) + 事件驱动准入执行器(run_contract)。
冻结契约: T+1成交, 信号日(T-1)快照预算(as-of安全), buy-once(交易级预算/原始股份上限), 缺价PENDING(V2aR恢复时重确认), FIFO资金来源账本(含RESERVE_RELEASE), 独立守恒恒等式与来源守恒断言, fill ledger。
冻结基准: 单股MU V0=49428.38, V2aF=15170.70 (budget=100, COST=0.0015, HOLD=120)。"""
import numpy as np
import pandas as pd
from collections import deque

COST = 0.0015
HOLD, DEDUP = 120, 60


def fsm_decide(mode, state, entry_i, i_own, below, in_ev, hold_days=None):
    if state == "FLAT":
        return "ENTER" if in_ev else None
    if state == "FULL":
        if (i_own - entry_i) >= (hold_days if hold_days else HOLD):
            return "EXIT"
        if mode == "V2aF" and below:
            return "HALVE"
        if mode == "V2aR":
            return "V2AR_TGT"
        return None
    if state == "FROZEN":
        return "EXIT" if (i_own - entry_i) >= (hold_days if hold_days else HOLD) else None
    return None


def v2ar_target(below, entry_budget, entry_shares, shares, p):
    frac = 0.5 if below else 1.0
    tgt = frac * entry_budget
    cur = shares * p
    if cur > tgt:
        return "SELL", min(shares, (cur - tgt) / p)
    max_buy_cash = max(0.0, entry_shares - shares) * p / (1 - COST)
    return "BUY", max(0.0, min(tgt - cur, max_buy_cash))


def run_contract(universe, score_snap, risk_mode, px_d, ma100_d, ev_d, listed_d, dt_index, ownpos_d, budget=100.0, collect=None, reserve_mode=False, hold_days=None, block=None, reentry_after_exit=False, init_state=None, return_state=False, plan_only=False, exec_plan=None, config_extra=None, require_config=False):
    block = block or set()
    if init_state is not None:
        cash = float(init_state["cash"])
        reserve = dict(init_state["reserve"])
        sources = deque(init_state["sources"])
        state = dict(init_state["state"])
        entry_i = dict(init_state["entry_i"])
        entry_budget = dict(init_state["entry_budget"])
        entry_shares = dict(init_state["entry_shares"])
        shares = dict(init_state["shares"])
        pending = dict(init_state["pending"])
        stale = dict(init_state["stale"])
        if init_state.get("last_date") is not None:
            assert dt_index[0] > init_state["last_date"], f"重启日期必须晚于已处理日 {init_state['last_date']}"
        cfg = init_state.get("config")
        if cfg:
            assert cfg.get("risk_mode") == risk_mode, f"配置不符: risk_mode {cfg.get('risk_mode')} vs {risk_mode}"
            assert cfg.get("hold_days") == (hold_days if hold_days else HOLD), f"配置不符: hold_days"
            assert tuple(cfg.get("universe", ())) == tuple(universe), "配置不符: universe"
            if config_extra is not None:
                for k2, v2 in config_extra.items():
                    assert cfg.get(k2) == v2, f"配置不符: {k2} {cfg.get(k2)} vs {v2}"
            if init_state.get("config_hash"):  # P0: 哈希与内容一致性(防篡改)
                import hashlib as _hl0
                import json as _j0
                _hh = _hl0.sha1(_j0.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12]
                assert _hh == init_state["config_hash"], f"配置哈希不一致: {init_state['config_hash']} vs {_hh}"
            elif require_config:
                assert False, "快照缺少config_hash, 前向模式拒绝恢复"
        elif require_config:
            assert False, "快照缺少配置签名, 拒绝恢复(前向模式)"
    else:
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
    if init_state is not None:
        fees = float(init_state.get("fees", 0.0))  # P0: 累计费用跨段恢复
        halve_proceeds = float(init_state.get("halve_proceeds", 0.0))
    hd = hold_days if hold_days else HOLD
    if hold_days is not None:
        assert isinstance(hold_days, int) and hold_days > 0, f"hold_days须为正整数: {hold_days}"
        hd = hold_days
    cash_h = pd.Series(np.nan, index=dt_index)
    funnel = dict(n1=0, n2=0, n3=0, n4=0, r_cash=0, r_price=0, r_rank=0, reconf=0, halve_funded_n=0, halve_funded_amt=0.0, collide=0)
    rejs, fills, collisions = [], [], []
    for k, d in enumerate(dt_index):
        if k == 0 and init_state is None:
            eq.iloc[0] = cash + sum(reserve.values())
            continue
        sd = init_state["last_date"] if k == 0 else dt_index[k - 1]  # 增量重放: 首日为完整处理日, 信号日=上段末日
        mkey = sd.to_period("M")
        px_today = {s: px_d[s].get(d, np.nan) for s in universe}
        px_yest = {s: px_d[s].get(sd, np.nan) for s in universe}
        stale_prev = dict(stale)
        # P0-1: 先用旧stale计算T-1快照(不读今日价), 再更新stale
        val_sig = {s: (px_yest[s] if np.isfinite(px_yest[s]) else stale[s]) for s in universe}
        for s in universe:
            if np.isfinite(px_today[s]):
                stale[s] = px_today[s]
        if exec_plan is not None:
            nav_sig = float(exec_plan["signal_nav"])  # P0: 预算冻结 — 使用T日冻结NAV, 不受T日数据修订影响
        else:
            nav_sig = cash + sum(reserve.values()) + sum(shares[s] * val_sig[s] for s in universe if shares[s] > 0)
        nav_prev = cash + sum(reserve.values()) + sum(shares[s] * stale_prev[s] for s in universe if shares[s] > 0)
        shares_prev = dict(shares)
        reserve_prev = sum(reserve.values())
        day_fee = 0.0
        orders = {}
        if exec_plan is not None:
            # P0: 计划驱动执行 — 消费已冻结的T日计划, 不重新决策/排名
            assert exec_plan.get("date") == d and exec_plan.get("sd") == sd, f"计划与执行日不匹配: plan={exec_plan.get('date')}/sd={exec_plan.get('sd')} vs d={d}/sd={sd}"
            if require_config:  # P0前向模式: 四项哈希缺一不可
                for _hk in ("config_hash", "state_hash", "input_hash", "plan_hash"):
                    assert exec_plan.get(_hk), f"计划缺少{_hk}, 前向模式拒绝执行"
            if exec_plan.get("plan_hash"):  # P0: 计划内容防篡改(plan_hash覆盖除自身外全部字段)
                import hashlib as _hl3
                import json as _j3
                _body = {k: v for k, v in exec_plan.items() if k != "plan_hash"}
                _ph = _hl3.sha1(_j3.dumps(_enc(_body), sort_keys=True, default=str).encode()).hexdigest()[:12]
                assert exec_plan["plan_hash"] == _ph, f"计划内容被修改: plan_hash {exec_plan['plan_hash']} vs {_ph}"
            if init_state is not None and exec_plan.get("state_hash"):  # P0: 计划必须产生于当前状态
                import hashlib as _hl2
                import json as _j2
                _sh = _hl2.sha1(_j2.dumps(_enc(dict(init_state)), sort_keys=True, default=str).encode()).hexdigest()[:12]
                assert exec_plan["state_hash"] == _sh, f"计划状态身份不符: plan={exec_plan['state_hash']} vs state={_sh}"
            if init_state is not None and init_state.get("config_hash") and exec_plan.get("config_hash"):
                assert exec_plan["config_hash"] == init_state["config_hash"], f"计划配置身份不符: {exec_plan.get('config_hash')} vs {init_state.get('config_hash')}"
            pending.clear()  # 计划已含恢复动作; 未成交时按计划元组重新入队
        for s in list(pending.keys()):
            if exec_plan is not None:
                break
            act, pw, sig_d = pending[s]
            if np.isfinite(px_today[s]) and px_today[s] > 0 and listed_d[s].get(d, False):
                if act == "V2AR_TGT":
                    below_now = bool(px_yest[s] < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
                    new_act = fsm_decide(risk_mode, state[s], entry_i[s], ownpos_d[s].get(sd, -1), below_now, ownpos_d[s].get(sd, -1) in ev_d[s], hd)
                    if new_act != act:
                        funnel["reconf"] += 1
                    act = new_act if new_act else act
                orders[s] = (act, pw, sig_d)
                pending.pop(s)
            else:
                funnel["r_price"] += 1
        cands = []
        for s in (universe if exec_plan is None else []):
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
                if (s, sd) in block:
                    funnel["blocked"] = funnel.get("blocked", 0) + 1
                    continue
                if np.isfinite(sc):
                    funnel["n2"] += 1
                    cands.append((sc, s))
                else:
                    funnel["r_rank"] += 1
                continue
            act = fsm_decide(risk_mode, state[s], entry_i[s], i_own, below, in_ev, hd)
            if act == "EXIT" and in_ev and reentry_after_exit:
                sc = score_snap.get((mkey, s), np.nan)
                if (s, sd) not in block and np.isfinite(sc):
                    cands.append((sc, s))  # 反事实: 退出后同日重评入场
            if act == "EXIT" and in_ev and state[s] != "FLAT" and (i_own - entry_i[s]) >= hd:
                funnel["collide"] += 1  # HOLD到期与新事件同日: FSM优先退出(冻结), 统计冲突
                collisions.append((s, d))
            if act:
                orders[s] = (act, 0.0, sd)
        cands.sort(reverse=True)
        reenters = []
        for r, (sc, s) in enumerate(cands):
            if r <= 1:
                funnel["n3"] += 1
            else:
                funnel["r_rank"] += 1
            if r <= 1 and state[s] == "FLAT":
                orders[s] = ("ENTER", (1.0 if len(cands) == 1 else (0.6 if r == 0 else 0.4)), sd)
            elif r <= 1 and state[s] != "FLAT" and reentry_after_exit:
                reenters.append((s, (1.0 if len(cands) == 1 else (0.6 if r == 0 else 0.4)), sd))
        if exec_plan is not None:
            # P0消费计划: 以冻结计划覆盖(本日pending已清空、cands为空, 计划为唯一订单来源)
            orders = dict(exec_plan["orders"])
            reenters = list(exec_plan["reenters"])
        reenters_names = {s for s, _, _ in reenters}
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
        for s, pw, sig_d in reenters:  # 反事实: EXIT已成交后同日重评入场(现金已释放)
            if not (np.isfinite(px_today[s]) and px_today[s] > 0 and listed_d[s].get(d, False)):
                continue
            buys[s] = (pw * nav_sig, pw, sig_d)
        if plan_only:
            assert exec_plan is None, "plan_only与exec_plan互斥"
            # 影子模式T日收盘计划: 只读决策, 不动状态/现金/账本 (与apply_day共用冻结决策原语)
            if collect is not None:
                collect["plan"] = {"date": d, "orders": {s: (a, pw, sd) for s, (a, pw, sd) in orders.items()}, "reenters": list(reenters), "funnel": dict(funnel)}
            return eq, sh_h, fees
        need = sum(v[0] for v in buys.values())
        sc = min(1.0, cash / need) if need > 0 else 0.0
        for s, (amt, pw, sig_d) in buys.items():
            p = px_today[s]
            spend = min(amt * sc, cash)
            act = "ENTER" if (s not in orders or s in reenters_names) else orders[s][0]  # reentry同日重入场(EXIT已执行)
            if spend <= 1e-9:
                if act == "ENTER" and s not in reenters_names:
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
            elif act == "V2AR_TGT":
                fills.append((s, sig_d, d, "V2AR_BUY", spend * (1 - COST) / p, p, fee))  # P0: V2aR补买入账本
        nav_now = cash + sum(reserve.values()) + sum(shares[s] * stale[s] for s in universe if shares[s] > 0)
        for s in universe:
            assert (state[s] == "FLAT") == (shares[s] <= 1e-12), f"状态-股数不一致 {s} {d}: state={state[s]} shares={shares[s]}"
        assert cash >= -1e-9 and nav_now > 0
        # P0-3 真守恒恒等式: ΔNAV = Σ q_{t-1}ΔP − fee_t (+reserve变动净零)
        dnav = nav_now - nav_prev
        mkt = sum(shares_prev[s] * (stale[s] - stale_prev[s]) for s in universe if shares_prev[s] > 0)
        assert abs(dnav - (mkt - day_fee)) < 0.02, f"守恒失败 {d:.date()} {dnav:.4f} vs {mkt - day_fee:.4f}"
        assert abs(sum(a for _, a in sources) - cash) < 1e-8 * max(1.0, abs(cash)), f"来源账本不守恒 {d}"
        eq.iloc[k] = nav_now
        cash_h.iloc[k] = cash
        sh_h.iloc[k] = [shares[s] for s in universe]
    if collect is not None:
        collect.update(eq=eq, sh=sh_h, cash=cash_h, funnel=funnel, rejs=rejs, halve_proceeds=halve_proceeds, fills=fills, sources_end=list(sources), collisions=collisions)
        import hashlib as _hl
        import json as _json
        if init_state is not None and init_state.get("config"):
            cfg_full = dict(init_state["config"])  # P0: 配置身份跨日继承, 不因config_extra缺省而丢失
            if config_extra is not None:
                for k2, v2 in config_extra.items():
                    assert cfg_full.get(k2) == v2, f"配置不符: {k2} {cfg_full.get(k2)} vs {v2}"
        else:
            cfg_full = dict(risk_mode=risk_mode, hold_days=hd, universe=tuple(universe), **(config_extra or {}))
        cfg_hash = _hl.sha1(_json.dumps(cfg_full, sort_keys=True, default=str).encode()).hexdigest()[:12]
        collect["end_state"] = dict(cash=cash, reserve=dict(reserve), sources=list(sources), state=dict(state), entry_i=dict(entry_i), entry_budget=dict(entry_budget), entry_shares=dict(entry_shares), shares=dict(shares), pending=dict(pending), stale=dict(stale), last_date=dt_index[-1], fees=fees, halve_proceeds=halve_proceeds, config=cfg_full, config_hash=cfg_hash)
    return eq, shares, fees


# ===== 影子模式T日收盘计划接口(P0: 严格as-of, 只读截至T收盘可知数据) =====
def plan_next_day(universe, score_snap, risk_mode, px_d, ma100_d, ev_d, ownpos_d, state, d_next, hold_days=None, reentry_after_exit=False, block=None, calendar=None):
    """T日收盘后的T+1订单意图计划。sd=state['last_date']=T; 只读取T收盘价/MA100/T仓位/事件/月度评分, 不接触T+1行情。
    返回 {"date": d_next, "sd": sd, "orders": {s: (act, pw, sd)}, "reenters": [(s, pw, sd)]} — 与run_contract决策阶段逐字一致。"""
    block = block or set()
    hd = hold_days if hold_days else HOLD
    if hold_days is not None:
        assert isinstance(hold_days, int) and hold_days > 0, f"hold_days须为正整数: {hold_days}"
        hd = hold_days
    assert state.get("last_date") is not None and d_next > state["last_date"], "计划日必须晚于状态末日"
    if calendar is not None:  # P1漏日防护: 计划日必须是状态末日的紧邻下一交易日
        assert d_next in calendar and state["last_date"] in calendar, "计划日/状态末日不在日历中"
        assert calendar.get_loc(d_next) == calendar.get_loc(state["last_date"]) + 1, f"漏日: {state['last_date']} 与 {d_next} 之间存在未处理交易日"
    sd = state["last_date"]
    mkey = sd.to_period("M")
    orders = {}
    for s in list(state["pending"].keys()):
        act, pw, sig_d = state["pending"][s]
        if act == "V2AR_TGT":  # 方案B: 恢复时以T日状态重确认(镜像run_contract)
            below_now = bool(px_d[s].get(sd, np.nan) < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
            new_act = fsm_decide(risk_mode, state["state"][s], state["entry_i"][s], ownpos_d[s].get(sd, -1), below_now, ownpos_d[s].get(sd, -1) in ev_d[s], hd)
            act = new_act if new_act else act
        orders[s] = (act, pw, sig_d)
    cands, reenters = [], []
    for s in universe:
        if s in orders:
            continue
        px_yest = px_d[s].get(sd, np.nan)
        if not np.isfinite(px_yest):
            continue
        i_own = ownpos_d[s].get(sd, -1)
        below = bool(px_yest < ma100_d[s].get(sd, np.nan)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else False
        in_ev = i_own in ev_d[s]
        st_s = state["state"][s]
        if st_s == "FLAT" and in_ev:
            sc = score_snap.get((mkey, s), np.nan)
            if (s, sd) in block:
                continue
            if np.isfinite(sc):
                cands.append((sc, s))
            continue
        act = fsm_decide(risk_mode, st_s, state["entry_i"][s], i_own, below, in_ev, hd)
        if act == "EXIT" and in_ev and reentry_after_exit:
            sc = score_snap.get((mkey, s), np.nan)
            if (s, sd) not in block and np.isfinite(sc):
                cands.append((sc, s))
        if act:
            orders[s] = (act, 0.0, sd)
    cands.sort(reverse=True)
    for r, (sc, s) in enumerate(cands):
        w = (1.0 if len(cands) == 1 else (0.6 if r == 0 else 0.4)) if r <= 1 else None
        if w is None:
            continue
        if state["state"][s] == "FLAT":
            orders[s] = ("ENTER", w, sd)
        elif reentry_after_exit:
            reenters.append((s, w, sd))
    import hashlib as _hlp
    import json as _jp
    val_sig_p = {s: (px_d[s].get(sd, np.nan) if np.isfinite(px_d[s].get(sd, np.nan)) else state["stale"].get(s, np.nan)) for s in universe}
    signal_nav = float(state["cash"] + sum(state["reserve"].values()) + sum(state["shares"][s] * val_sig_p[s] for s in universe if state["shares"][s] > 0))
    state_hash = _hlp.sha1(_jp.dumps(_enc(dict(state)), sort_keys=True, default=str).encode()).hexdigest()[:12]
    input_hash = _hlp.sha1(_jp.dumps({"sd": str(sd), "mkey": str(mkey), "px": {s: val_sig_p[s] for s in universe}, "ma100": {s: (float(ma100_d[s].get(sd)) if np.isfinite(ma100_d[s].get(sd, np.nan)) else None) for s in universe}, "ev_pos": {s: (ownpos_d[s].get(sd, -1) in ev_d[s]) for s in universe}, "scores": {s: score_snap.get((mkey, s)) for s in universe}}, sort_keys=True, default=str).encode()).hexdigest()[:12]
    _plan = {"date": d_next, "sd": sd, "signal_nav": signal_nav, "orders": orders, "reenters": reenters, "config_hash": state.get("config_hash"), "state_hash": state_hash, "input_hash": input_hash}
    _plan["plan_hash"] = _hlp.sha1(_jp.dumps(_enc(dict(_plan)), sort_keys=True, default=str).encode()).hexdigest()[:12]
    return _plan


def apply_day(universe, score_snap, risk_mode, px_d, ma100_d, ev_d, listed_d, ownpos_d, state, plan, d_next, hold_days=None, budget=100.0, reserve_mode=False, collect=None, config_extra=None, require_config=False):
    """T+1执行已冻结的T日计划: 复用run_contract结算原语, 不重新选股/排名。返回collect(dict: eq/sh/cash/fills/end_state)。"""
    collect = {} if collect is None else collect
    run_contract(universe, score_snap, risk_mode, px_d, ma100_d, ev_d, listed_d, [d_next], ownpos_d, budget=budget, collect=collect, reserve_mode=reserve_mode, hold_days=hold_days, init_state=state, exec_plan=plan, config_extra=config_extra, require_config=require_config)
    return collect


# ===== 影子模式状态持久化(原子写入/版本校验/往返) =====
_STATE_VERSION = 1


def _enc(v):
    if isinstance(v, pd.Timestamp):
        return {"__ts__": v.isoformat()}
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    return v


def _dec(v):
    if isinstance(v, dict):
        if "__ts__" in v:
            return pd.Timestamp(v["__ts__"])
        return {k: _dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


def save_state(st, path):
    """原子写入状态快照(JSON), 带版本号; Timestamp/元组安全往返。"""
    import json
    import os
    import tempfile
    doc = {"version": _STATE_VERSION, "saved_at": pd.Timestamp.now().isoformat(), "state": _enc(dict(st))}
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, allow_nan=True)
        os.replace(tmp, path)  # 原子替换
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def load_state(path):
    import json
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    assert doc.get("version") == _STATE_VERSION, f"状态版本不匹配: {doc.get('version')}"
    return _dec(doc["state"])


