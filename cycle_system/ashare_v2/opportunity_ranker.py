# -*- coding: utf-8 -*-
"""opportunity_ranker — 可解释线性评分(阶段D实装, §4/§5.3)。

**未校准**: 输出是"相对偏好分数"(0-10分), 禁止解读为预期收益率/胜率(config.CALIBRATED=False,
测试锁措辞)。权重 config.SCORE_WEIGHTS 预设未寻优; 分项贡献 breakdown 全部可解释可回溯。
折价原则(§5.3): 数据可信度最低者不得为正边际依据——data_credibility 权重 0.20 直接惩罚
PROXY/BLOCKED; Strict 闸门在上游已拦截, 这里二次折价(纵深防御)。
"""
from __future__ import annotations

import math

from cycle_system.ashare_v2 import config as C
from cycle_system.ashare_v2.schemas import Availability, Engine

_STAGE_Q = {
    "BOTTOM_FORMING": 0.90, "EARLY_RECOVERY": 1.00, "EXPANSION": 0.45,
    "REVERSAL_CANDIDATE": 0.50, "ENTRY_CONFIRMED": 0.65, "REBOUND_ACTIVE": 0.55,
}
_CRED = {Availability.CONFIRMED.value: 1.0, Availability.DATA_LIMITED.value: 0.60,
         Availability.PROXY_ONLY.value: 0.35, Availability.DATA_BLOCKED.value: 0.10,
         Availability.UNKNOWN.value: 0.10}


def _clip01(v):
    return max(0.0, min(1.0, v))


def _lin(v, lo, hi):
    """v∈[lo,hi] → [0,1]; None→0.5中性(显式: 缺证据=中性而非满分)。"""
    if v is None:
        return 0.5
    return _clip01((v - lo) / (hi - lo))


def score_merged(merged: dict) -> dict:
    """对一条合并资金行评分: 取该 code 证据最强的代表记录评分(资金行视角), 两记录都留档。"""
    cands = [score_record(r, merged.get("age_days", {}).get(r.engine, {})) for r in merged["records"]]
    best = max(cands, key=lambda s: s["score_points"])
    best["code"] = merged["code"]
    best["engines"] = merged["engines"]
    best["record_ids"] = [id(r) for r in merged["records"]]
    if len(cands) > 1:
        best["runner_up_engine"] = cands[1]["engine"]
    return best


def score_record(rec, age_ctx: dict) -> dict:
    ev = {e.name: e for e in rec.key_evidence}
    feats = rec.features_at_signal or {}
    def val(name):
        e = ev.get(name)
        return None if e is None else e.value
    stage = _STAGE_Q.get(rec.cycle_stage, 0.30)
    if rec.engine == Engine.LONG_CYCLE.value:
        rp = val("ind_rp250")
        t250 = val("ind_trend250")
        mom = val("ind_mom12")
        long_ev = 0.5 * _lin(t250, -0.30, 0.30) + 0.3 * _lin(mom, -0.25, 0.25) + 0.2 * _clip01(1 - (rp or 0.5))
        rel = _lin(val("ind_rel_mkt_250"), -0.30, 0.30)
        amt = age_ctx.get("amt20")
        liq = 0.3 if amt is None else _clip01((math.log10(max(amt, 1.0)) - 7.0) / 1.5)
    else:
        dd = val("dd_from_high_250") or -0.18
        long_ev = 0.5 + 0.5 * _clip01((-dd - 0.18) / 0.20)
        rel = 0.45
        amt = age_ctx.get("amt20")
        liq = 0.3 if amt is None else _clip01((math.log10(max(amt, 1.0)) - 7.0) / 1.5)
    fresh = age_ctx.get("freshness")
    liq = _clip01(liq * (0.8 + 0.2 * (fresh if fresh is not None else 0.5)))
    cred = _CRED.get(rec.data_credibility, 0.1)
    comp = {
        "cycle_stage_quality": stage,
        "long_horizon_evidence": long_ev,
        "relative_strength": rel,
        "liquidity_executability": liq,
        "data_credibility": cred,
    }
    breakdown = {k: round(C.SCORE_WEIGHTS[k] * v, 4) for k, v in comp.items()}
    raw = sum(breakdown.values())
    return {"engine": rec.engine, "cycle_stage": rec.cycle_stage, "entry_kind": rec.entry_kind,
            "components": {k: round(v, 4) for k, v in comp.items()}, "breakdown": breakdown,
            "score_points": round(raw * 10, 3),
            "calibration_note": "未校准相对偏好分(0-10), 非预期收益率/胜率",
            "forbidden_claims_note": rec.forbidden_claims_note}
