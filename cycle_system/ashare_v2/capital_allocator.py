# -*- coding: utf-8 -*-
"""capital_allocator — 贪心资金分配 + 风险预算 + 换仓(阶段D实装, §5)。

规则(全部预设未寻优, config 锁):
  * 排序: score_points 降序(同分按 code 升序——确定性可重放, §12一致性);
  * 仓位规模: 纯风险驱动——单名日σ贡献 ≤ NAV*NAME_RISK_FRAC(0.10), 现金硬约束+
    CASH_RESERVE_FRAC 预留; **无固定仓位数(MAX_POSITIONS=None)/无固定引擎占比
    (ENGINE_SPLIT_FRAC=None)**: 引擎资金占比是结果不是约束;
  * 组合层: Σ日σ贡献 ≤ NAV*PORT_DAILY_RISK_FRAC(0.20), 超出→停止贪心(后续显式
    PORT_RISK_BUDGET_STOP, 不静默跳过);
  * 行业集中: 单行业权重>INDUSTRY_CONCENTRATION_ALLOWED(0.60)→拒同新增(拒因显式);
  * 风险预算态: ctx.budget_state=EXCEEDED(回撤/压力触发, 由组合层E供给)→冻结全部新增,
    forced_liquidation_priority=True(风险卖出覆盖换仓优先, §5.4);
  * 换仓: 资金不足/预算停止时, 挑战最弱持仓——需 edge≥SWAP_MIN_SCORE_EDGE 且预估费用×
    SWAP_COST_MULT ≤ edge*每分NAV价值上限预设(工程保守上限, **非收益宣称**);
    单日≤SWAP_DAILY_LIMIT 笔;
  * 守恒: Σ买入金额 ≤ spendable; 输出 rejects 覆盖每个被拒候选。
"""
from __future__ import annotations

from cycle_system.ashare_v2 import config as C

COST_RATE_EST = 0.0011            # 双边费用粗估(佣金+印花+过户量级; 精确口径在E层执行器)
NAV_VALUE_PER_SCORE_POINT = 0.01  # 每1评分点允许的NAV占比(保守工程上限, 未校准, 非收益率)


def allocate(scored: list, holdings: list, cash: float, nav: float, ctx: dict) -> dict:
    """scored: ranker输出列表; holdings: [{code,value,score_points,industry,vol20,engine}];
    ctx: {px:{code}, vol20:{code}, industry:{code}, budget_state, risk_stress:{code:mult}}"""
    px, vol, ind_of = ctx.get("px", {}), ctx.get("vol20", {}), ctx.get("industry", {})
    budget_state = ctx.get("budget_state", "NORMAL")
    spendable = max(0.0, cash - nav * C.CASH_RESERVE_FRAC)
    spendable0 = spendable
    ordered = sorted(scored, key=lambda s: (-s["score_points"], s["code"]))
    held_by_code = {h["code"]: h for h in holdings}
    ind_value = {}
    for h in holdings:
        ind_value[h.get("industry", "")] = ind_value.get(h.get("industry", ""), 0.0) + h["value"]
    buys, rejects, swaps = [], [], []
    risk_used = sum(h["value"] * h.get("vol20", 0.0) for h in holdings)
    risk_cap = nav * C.PORT_DAILY_RISK_FRAC
    greedy_stopped = budget_state == "EXCEEDED"
    if greedy_stopped:
        for s in ordered:
            rejects.append({"code": s["code"], "reason": "RISK_BUDGET_EXCEEDED_FROZEN",
                            "score_points": s["score_points"]})
        return _plan(buys, rejects, swaps, holdings, ind_value, 0.0, spendable0, nav, budget_state,
                     forced_priority=True, note="风险预算超限: 全部新增冻结(§5.4), 风险卖出覆盖换仓优先")
    for s in ordered:
        code = s["code"]
        if code in held_by_code:
            rejects.append({"code": code, "reason": "ALREADY_HELD_SINGLE_FUNDING",
                            "score_points": s["score_points"],
                            "note": "同股资金只承诺一次(§6.2); 双引擎归因已由signal_merge合并"})
            continue
        p, v = px.get(code), vol.get(code)
        if not p or not v or v <= 0:
            rejects.append({"code": code, "reason": "NO_PRICE_OR_VOL_DATA", "score_points": s["score_points"]})
            continue
        risk_shares = (nav * C.NAME_RISK_FRAC_OF_NAV) / (p * v)
        stress = (ctx.get("risk_stress", {}) or {}).get(code, 1.0)
        risk_shares /= stress
        cash_shares = spendable / p
        ind = ind_of.get(code, s.get("industry", ""))
        ind_allowed_val = max(0.0, C.INDUSTRY_CONCENTRATION_ALLOWED * nav - ind_value.get(ind, 0.0)) if ind else float("inf")
        shares = int(min(risk_shares, cash_shares, ind_allowed_val / p))   # 行业60%硬顶=截断至余量(非跳过)
        base, step = (200, 1) if code.startswith("68") else (100, 100)     # 申报规则与冻结执行器同源
        if shares < base:
            if ind and (ind_value.get(ind, 0.0) / nav) >= C.INDUSTRY_CONCENTRATION_ALLOWED - 1e-9:
                rejects.append({"code": code, "reason": "INDUSTRY_CONCENTRATION_LIMIT", "industry": ind,
                                "industry_w_now": round(ind_value.get(ind, 0.0) / nav, 4),
                                "allowed": C.INDUSTRY_CONCENTRATION_ALLOWED, "score_points": s["score_points"]})
                continue
            r = {"code": code, "reason": "INSUFFICIENT_CASH_FOR_ONE_LOT", "score_points": s["score_points"],
                 "one_lot_cost": round(base * p, 2), "spendable_left": round(spendable, 2)}
            swap = _try_swap(r, s, holdings, held_by_code, swaps, p, v, nav)
            if not swap:
                rejects.append(r)
            continue
        shares = base + ((shares - base) // step) * step                   # 整手向下取整(避免执行器批量拒单)
        if shares <= 0:
            r = {"code": code, "reason": "INSUFFICIENT_CASH", "score_points": s["score_points"]}
            swap = _try_swap(r, s, holdings, held_by_code, swaps, p, v, nav)
            if not swap:
                rejects.append(r)
            continue
        val = shares * p
        add_risk = val * v
        if add_risk / nav > C.NAME_RISK_FRAC_OF_NAV + 1e-9:      # 截断后仍超单名风险(不应发生, 显式防御)
            rejects.append({"code": code, "reason": "NAME_RISK_CAP", "score_points": s["score_points"]})
            continue
        if risk_used + add_risk > risk_cap:
            r = {"code": code, "reason": "PORT_RISK_BUDGET_STOP", "score_points": s["score_points"],
                 "risk_used_after_would_be": round((risk_used + add_risk) / nav, 4),
                 "cap": C.PORT_DAILY_RISK_FRAC}
            greedy_stopped = True
            swap = _try_swap(r, s, holdings, held_by_code, swaps, p, v, nav)
            if not swap:
                rejects.append(r)
                break
            continue
        buys.append({"code": code, "shares": shares, "value": round(val, 2), "px": p,
                     "score_points": s["score_points"], "engines": s.get("engines", [s.get("engine")]),
                     "industry": ind, "risk_contrib_nav": round(val * v / nav, 4),
                     "record_ids": s.get("record_ids", [])})
        spendable -= val
        risk_used += add_risk
        if ind:
            ind_value[ind] = ind_value.get(ind, 0.0) + val
    if greedy_stopped:
        done = {b["code"] for b in buys} | {r["code"] for r in rejects} | {w["in"] for w in swaps}
        for s in ordered:
            if s["code"] not in done:
                rejects.append({"code": s["code"], "reason": "PORT_RISK_BUDGET_STOP",
                                "score_points": s["score_points"]})
    return _plan(buys, rejects, swaps, holdings, ind_value, spendable, spendable0, nav,
                 budget_state, forced_priority=False, note="")


def _try_swap(reject_r, s, holdings, held_by_code, swaps, p, v, nav):
    if len(swaps) >= C.SWAP_DAILY_LIMIT or not holdings:
        return False
    weakest = min((h for h in holdings if not h.get("risk_forced")), key=lambda h: h["score_points"])
    edge = s["score_points"] - weakest["score_points"]
    cand_val = min(nav * C.NAME_RISK_FRAC_OF_NAV / max(p * v, 1e-9) * p, nav * 0.25)
    est_cost = (weakest["value"] + cand_val) * COST_RATE_EST
    edge_cap = edge * NAV_VALUE_PER_SCORE_POINT * nav
    if edge < C.SWAP_MIN_SCORE_EDGE or edge_cap < est_cost * C.SWAP_COST_MULT:
        reject_r["swap_denied"] = {"out": weakest["code"], "edge": round(edge, 3),
                                   "min_edge": C.SWAP_MIN_SCORE_EDGE,
                                   "est_cost": round(est_cost, 2), "edge_cap": round(edge_cap, 2),
                                   "why": "edge<阈值 或 费用×倍数>保守上限(未校准工程判据)"}
        return False
    swaps.append({"out": weakest["code"], "in": s["code"], "edge": round(edge, 3),
                  "est_cost": round(est_cost, 2), "in_score": s["score_points"],
                  "out_score": weakest["score_points"],
                  "in_engines": s.get("engines", [s.get("engine")]),
                  "in_industry": s.get("industry", ""),
                  "in_vol": v, "in_px": p, "out_value": round(weakest["value"], 2),
                  "out_vol": weakest.get("vol20"),
                  "note": "换仓成本判据为工程保守上限, 非预期收益宣称"})
    return True


def _plan(buys, rejects, swaps, holdings, ind_value, spendable_left, spendable0, nav,
          budget_state, forced_priority, note):
    spent = sum(b["value"] for b in buys)
    eng_val = {}
    for b in buys:
        for e in b["engines"]:
            eng_val[e] = eng_val.get(e, 0.0) + b["value"]
    return {
        "buys": buys, "rejects": rejects, "swaps": swaps,
        "cash_conservation": {"spendable_before": round(spendable0, 2), "spent": round(spent, 2),
                               "spendable_after": round(spendable0 - spent, 2),
                               "ok": spent <= spendable0 + 1e-6},
        "engine_funding": {k: round(valor, 2) for k, valor in eng_val.items()},
        "engine_frac": {k: round(valor / max(spent, 1e-9), 4) for k, valor in eng_val.items()},
        "engine_split_constraint": C.ENGINE_SPLIT_FRAC,
        "max_positions_constraint": C.MAX_POSITIONS,
        "industry_weights_after": {k: round(val / nav, 4) for k, val in ind_value.items()},
        "position_count": len(holdings) + len(buys) - len(swaps),
        "risk_budget_state": budget_state, "forced_liquidation_priority": forced_priority,
        "note": note or "贪心=分数序+可行性; 无固定持仓数/引擎占比(§5.1)",
        "calibration_note": "评分未校准: 分配是规则化相对偏好结果, 非收益率优化",
    }
