# -*- coding: utf-8 -*-
"""阶段D工程测试(§9.2 合成场景 E/F/G/H + §12 资金一致性与守恒 + 措辞纪律)。"""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from cycle_system.ashare_v2 import capital_allocator as K                      # noqa: E402
from cycle_system.ashare_v2 import config as C                                 # noqa: E402
from cycle_system.ashare_v2 import opportunity_ranker as R                     # noqa: E402
from cycle_system.ashare_v2 import signal_merge as M                           # noqa: E402
from cycle_system.ashare_v2.schemas import (Availability, DataMode, Engine, Evidence,  # noqa: E402
                                            EntryKind, LongCycleState, MembershipTrust,
                                            OpportunityRecord, FastVState)


def _rec(code, engine=Engine.LONG_CYCLE.value, stage=LongCycleState.EARLY_RECOVERY.value,
         entry=EntryKind.BREAKOUT_CONFIRM.value, cred=Availability.CONFIRMED.value,
         ev=None, feats=None):
    return OpportunityRecord(
        industry="IND" + code[-1], industry_taxonomy_version="V", membership_trust=MembershipTrust.CURRENT_SNAPSHOT_ONLY.value,
        code=code, engine=engine, cycle_stage=stage,
        key_evidence=ev or [Evidence("ind_rp250", 0.3, "2026-06-30", "t", Availability.CONFIRMED.value),
                            Evidence("ind_trend250", 0.1, "2026-06-30", "t", Availability.CONFIRMED.value),
                            Evidence("ind_mom12", 0.05, "2026-06-30", "t", Availability.CONFIRMED.value),
                            Evidence("ind_rel_mkt_250", 0.1, "2026-06-30", "t", Availability.CONFIRMED.value)],
        first_signal_day="2026-06-30", features_at_signal=feats or {"rp120": 0.5, "trend60": 0.02,
                                                                    "trend250": 0.1, "ma60": 1, "ma120": 1},
        entry_kind=entry, entry_condition="x", invalidation_condition="y", research_horizon_m=6,
        risk_estimate=None, data_credibility=cred, decision="NO_TRADE", decision_reason="test")


def _scored(code, pts, engines=None):
    return {"code": code, "score_points": pts, "engines": engines or [Engine.LONG_CYCLE.value],
            "industry": "", "record_ids": [1]}


# ── E: 高分但资金不足→贪心取次优可行, 顺序与守恒 ───────────
def test_E_greedy_insufficient_cash_and_order():
    scored = [_scored("A", 9.0), _scored("B", 8.0), _scored("C", 7.0)]
    ctx = {"px": {"A": 10, "B": 10, "C": 10}, "vol20": {"A": 0.35, "B": 0.01, "C": 0.01},
           "industry": {"A": "IA", "B": "IB", "C": "IC"}}
    plan = K.allocate(scored, [], cash=8000, nav=20000, ctx=ctx)
    codes = [b["code"] for b in plan["buys"]]
    assert codes[0] == "A", "贪心首购必须是最高分"
    assert plan["cash_conservation"]["ok"] and plan["cash_conservation"]["spent"] <= 7000 + 1e-6
    reasons = {r["code"]: r["reason"] for r in plan["rejects"]}
    assert reasons.get("C") == "INSUFFICIENT_CASH_FOR_ONE_LOT", "剩余资金不足一手须显式拒因(整手对齐)"
    assert len(codes) == 2 and codes[1] == "B"


# ── F: 换仓阈值与成本覆盖 ─────────────────────────────────
def test_F_swap_edge_and_cost_gate():
    holdings = [{"code": "X", "value": 1500.0, "score_points": 2.0, "industry": "IX", "vol20": 0.05}]
    ctx = {"px": {"Y": 100, "Z": 100}, "vol20": {"Y": 0.05, "Z": 0.05}, "industry": {"Y": "IY", "Z": "IZ"}}
    weak_edge = [_scored("Z", 2.3)]
    plan1 = K.allocate(weak_edge, holdings, cash=10, nav=20000, ctx=ctx)
    assert plan1["swaps"] == []
    zr = next(r for r in plan1["rejects"] if r["code"] == "Z")
    assert zr["reason"] == "INSUFFICIENT_CASH_FOR_ONE_LOT" and zr["swap_denied"]["edge"] < C.SWAP_MIN_SCORE_EDGE
    strong = [_scored("Y", 9.0)]
    plan2 = K.allocate(strong, holdings, cash=10, nav=20000, ctx=ctx)
    assert len(plan2["swaps"]) == 1
    sw = plan2["swaps"][0]
    assert sw["out"] == "X" and sw["in"] == "Y" and sw["edge"] >= C.SWAP_MIN_SCORE_EDGE
    assert sw["est_cost"] * C.SWAP_COST_MULT <= sw["edge"] * K.NAV_VALUE_PER_SCORE_POINT * 20000 + 1e-6
    lim = [_scored("Y", 9.0), _scored("W", 8.9), _scored("V", 8.8)]
    ctx3 = dict(ctx); ctx3["px"].update({"W": 100, "V": 100}); ctx3["vol20"].update({"W": 0.05, "V": 0.05})
    ctx3["industry"] = {"Y": "IY", "W": "IW", "V": "IV"}
    hold2 = [{"code": "X", "value": 1500, "score_points": 2.0, "industry": "IX", "vol20": 0.05},
             {"code": "X2", "value": 1500, "score_points": 1.0, "industry": "IX2", "vol20": 0.05}]
    plan3 = K.allocate(lim, hold2, cash=10, nav=20000, ctx=ctx3)
    assert len(plan3["swaps"]) <= C.SWAP_DAILY_LIMIT


# ── G: 行业集中硬顶 + 引擎占比是结果不是约束 ───────────────
def test_G_industry_cap_and_no_engine_split():
    same_ind = [_scored("A", 9.5), _scored("B", 9.4)]
    ctx = {"px": {"A": 10, "B": 10}, "vol20": {"A": 0.005, "B": 0.005}, "industry": {"A": "I", "B": "I"}}
    plan = K.allocate(same_ind, [], cash=60000, nav=20000, ctx=ctx)
    w = plan["industry_weights_after"].get("I", 0)
    assert w <= C.INDUSTRY_CONCENTRATION_ALLOWED + 1e-9, "行业权重不得超过60%硬顶"
    assert any(r["reason"] == "INDUSTRY_CONCENTRATION_LIMIT" for r in plan["rejects"]), "余量耗尽必须显式拒"
    lc_heavy = [_scored("L1", 9.5, [Engine.LONG_CYCLE.value]), _scored("L2", 9.3, [Engine.LONG_CYCLE.value]),
                _scored("F1", 3.0, [Engine.FAST_V.value])]
    ctx2 = {"px": {"L1": 10, "L2": 10, "F1": 10}, "vol20": {"L1": 0.30, "L2": 0.30, "F1": 0.30},
            "industry": {"L1": "A", "L2": "B", "F1": "C"}}
    plan2 = K.allocate(lc_heavy, [], cash=9000, nav=20000, ctx=ctx2)   # 资金只够高分LC
    assert plan2["engine_split_constraint"] is None and plan2["max_positions_constraint"] is None
    frac = plan2["engine_frac"]
    assert not (abs(frac.get(Engine.LONG_CYCLE.value, 1) - 0.4) < 1e-9), "禁止恰好40/60固定分割"
    assert frac.get(Engine.LONG_CYCLE.value, 0) > 0.9 or Engine.FAST_V.value not in frac, \
        "高分单引擎拿全部资金必须合法(占比=结果)"


# ── H: 组合风险预算停止贪心 + 超额冻结 ─────────────────────
def test_H_portfolio_risk_budget_stop_and_freeze():
    s = [_scored("A", 9), _scored("B", 8), _scored("C", 7)]
    # 单元测试专注风险预算机制: industry留空避免60%行业顶干扰; cash>nav的口径一致性由E层组合负责
    ctx = {"px": {"A": 100, "B": 100, "C": 100}, "vol20": {"A": 0.05, "B": 0.05, "C": 0.05},
           "industry": {}}
    plan = K.allocate(s, [], cash=100000, nav=20000, ctx=ctx)
    assert len(plan["buys"]) == 2
    stop = [r for r in plan["rejects"] if r["reason"] == "PORT_RISK_BUDGET_STOP"]
    assert len(stop) >= 1
    plan2 = K.allocate(s, [], cash=100000, nav=20000,
                       ctx={**ctx, "budget_state": "EXCEEDED"})
    assert plan2["buys"] == [] and plan2["forced_liquidation_priority"] is True
    assert all(r["reason"] == "RISK_BUDGET_EXCEEDED_FROZEN" for r in plan2["rejects"])


# ── 一致性与确定性(§12) ───────────────────────────────────
def test_determinism_replay():
    scored = [_scored("B", 8.0), _scored("A", 9.0), _scored("C", 8.0)]
    ctx = {"px": {"A": 10, "B": 10, "C": 10}, "vol20": {"A": 0.35, "B": 0.01, "C": 0.01},
           "industry": {}}
    p1 = K.allocate(scored, [], cash=8000, nav=20000, ctx=ctx)
    p2 = K.allocate(scored[::-1], [], cash=8000, nav=20000, ctx=ctx)
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True), "同输入乱序必须同输出(平局按code)"


# ── 同股双引擎: 资金单承诺 + 评分取强代表 ─────────────────
def test_same_code_single_funding():
    lc, fv = _rec("600001"), _rec("600001", engine=Engine.FAST_V.value, stage=FastVState.ENTRY_CONFIRMED.value,
                                 entry="REVERSAL_CANDIDATE",
                                 ev=[Evidence("dd_from_high_250", -0.30, "2026-06-30", "t", Availability.CONFIRMED.value)])
    merged = M.merge_candidates([lc], [fv])
    assert len(merged) == 1 and len(merged[0]["records"]) == 2
    assert "资金承诺一条" in merged[0]["merge_note"]
    s = R.score_merged(merged[0])
    assert len(s["record_ids"]) == 2 and s["engines"] == [Engine.FAST_V.value, Engine.LONG_CYCLE.value]
    ctx = {"px": {"600001": 10}, "vol20": {"600001": 0.02}, "industry": {"600001": "IND1"}}
    plan = K.allocate([s], [], cash=20000, nav=20000, ctx=ctx)
    assert len(plan["buys"]) == 1 and plan["buys"][0]["engines"] == [Engine.FAST_V.value, Engine.LONG_CYCLE.value]


# ── 评分可解释 + 折价 + 措辞纪律 ──────────────────────────
def test_score_explainability_and_credibility_discount():
    good = R.score_record(_rec("600002", cred=Availability.CONFIRMED.value), {})
    blocked = R.score_record(_rec("600002", cred=Availability.PROXY_ONLY.value), {})
    assert good["score_points"] > blocked["score_points"], "可信度更低必须折价(§5.3)"
    assert set(good["breakdown"]) == set(C.SCORE_WEIGHTS)
    s = 0.0
    for k, w in C.SCORE_WEIGHTS.items():
        assert abs(C.SCORE_WEIGHTS[k] - w) < 1e-12
        s += w
    assert abs(s - 1.0) < 1e-9
    assert "未校准" in good["calibration_note"]


def test_no_forbidden_return_claims():
    scored = [_scored("A", 9.0)]
    ctx = {"px": {"A": 10}, "vol20": {"A": 0.05}, "industry": {}}
    blob = json.dumps(K.allocate(scored, [], cash=9000, nav=20000, ctx=ctx), ensure_ascii=False)
    for w in ("预期收益率", "胜率", "expected return", "win rate", "概率优势"):
        assert w not in blob, "分配产物不得含收益/胜率话术(免责声明仅在评分侧, 且明确禁止此类宣称)"


def test_ranker_stage_ordering():
    er = R.score_record(_rec("600003", stage=LongCycleState.EARLY_RECOVERY.value), {})
    bf = R.score_record(_rec("600003", stage=LongCycleState.BOTTOM_FORMING.value), {})
    ex = R.score_record(_rec("600003", stage=LongCycleState.EXPANSION.value,
                             entry=EntryKind.TREND_CONTINUATION.value), {})
    assert er["components"]["cycle_stage_quality"] > bf["components"]["cycle_stage_quality"] > ex["components"]["cycle_stage_quality"]
