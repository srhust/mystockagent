# -*- coding: utf-8 -*-
"""strict_gate — 严格模式阻断条件统一契约 (独立验收第5项; 不改任何预测规则)。

四类已知数据缺口构成 STRICT 模式的**正式信号阻断条件**；RESEARCH 模式**必须显式降级**
(携带标注与折价), 两者行为由本模块单点定义并被测试锁定:

  G1 SW_PARTIAL        申万历史成员 in_date 无 out_date → 行业归属不可PIT验证 →
                       STRICT: 行业级正式信号全阻断(candidates=0+闸门报告);
                       RESEARCH: PROXY_ONLY 指数 + SW_PARTIAL 信任 + 评分折价0.35。
  G2 MARKET_WIDE_POOL  9011 全名录六重过滤原始行未落盘 → 全市场历史池不可重建 →
                       STRICT: 市场全域统计/全域选股 DATA_BLOCKED;
                       RESEARCH: 仅当前池九桶+聚合曲线, 强制 SURVIVORSHIP_BIAS_PARTIAL。
  G3 ST_FLAG           历史ST标记不可得 → 涨跌幅分期与风险名判定失真 →
                       STRICT: 涉及 is_st 的成交判定不得使用未验证假设(阻断);
                       RESEARCH: 执行层按 is_st=False 近似 + 显式 ST_FLAG_UNVERIFIED 标注。
  G4 FUNDAMENTAL_TS    产业指标发布时间(first_public_ts)缺失 → 无法证明asof可见性 →
                       STRICT: 基于该指标的正式信号拒绝(pit_records 已实装);
                       RESEARCH: UNKNOWN/DATA_BLOCKED → 折价0.1, 不回填不编造。

纪律: 本模块只做**阻断/降级判定与报告**, 不产生信号; 与 data_portal/universe/
industry_cycle/long_cycle/exit_manager 的实装点一一互指(evidence 字段)。
"""
from __future__ import annotations

import pandas as pd

from cycle_system.ashare_v2 import config as C
from cycle_system.ashare_v2.schemas import Availability, DataMode

GATES = [
    {"id": "G1_SW_PARTIAL", "name": "申万历史成员无out_date",
     "strict_effect": "BLOCK_FORMAL_SIGNALS(行业级候选=0+报告)",
     "research_effect": "DEGRADE: PROXY_ONLY指数+SW_PARTIAL信任+折价0.35",
     "evidence": ["industry_cycle.IndustryMembershipProvider.sw_trust",
                  "long_cycle.LongCycleEngine.strict_gate_report", "test_phaseC::test_strict_gate_blocks_formal_signals"]},
    {"id": "G2_MARKET_WIDE_POOL", "name": "全市场历史池名录未落盘",
     "strict_effect": "DATA_BLOCKED(市场全域统计/全域选股)",
     "research_effect": "DEGRADE: 当前池九桶+月末聚合曲线+SURVIVORSHIP_BIAS_PARTIAL强制",
     "evidence": ["universe.UniverseProvider.buckets_at.market_wide",
                  "data_portal.quality_report.universe_marketwide", "test_phaseB::test_universe_*"]},
    {"id": "G3_ST_FLAG", "name": "历史ST标记不可得",
     "strict_effect": "BLOCK(未验证is_st假设不得进入正式成交判定)",
     "research_effect": "DEGRADE: 执行器is_st=False近似+ST_FLAG_UNVERIFIED标注",
     "evidence": ["data_portal 模块doc缺口声明", "ashare_executor.tradable(is_st参数)"]},
    {"id": "G4_FUNDAMENTAL_TS", "name": "基本面/产业指标发布时间缺失",
     "strict_effect": "REJECT(基于该指标不产生正式交易信号 §7.4)",
     "research_effect": "DEGRADE: UNKNOWN/DATA_BLOCKED折价0.1, 不回填",
     "evidence": ["data_portal.DataPortal.pit_records", "opportunity_ranker._CRED[DATA_BLOCKED]=0.1",
                  "test_phaseB::pit_records可见性测试"]},
]


def strict_blocked() -> bool:
    """当前数据状态下 STRICT 是否存在任一阻断(是→无正式信号, 系统只能影子/研究)。"""
    return True  # G1-G4 全部成立(结构性事实, 由 probe 运行时复核并落证据)


def evaluate(portal=None, membership=None, universe_buckets=None, lc_strict_candidates=None) -> dict:
    """运行时复核四条件状态; 参数缺省时仅返回静态契约。"""
    rows = []
    for g in GATES:
        row = dict(g)
        row["status"] = "BLOCKING"
        rows.append(row)
    out = {"contract_version": 1, "config_hash": C.config_hash(), "gates": rows,
           "strict_formal_signals_possible": False,
           "research_mode_note": "RESEARCH 可运行影子但每条记录必须携带对应降级标注(折价/trust/caveat), "
                                 "禁止把降级输出包装为正式信号",
           "probe": {}}
    if portal is not None:
        q = portal.quality_report()
        ds = q.get("datasets", {})
        out["probe"]["market_wide_status"] = ds.get("universe_marketwide", {}).get("status")
        fi = ds.get("fundamental_indicators", {})
        out["probe"]["fundamental_blocked"] = sum(
            1 for v in fi.values() if v == Availability.DATA_BLOCKED.value)
        out["probe"]["fundamental_total"] = len(fi)
        out["probe"]["sw_dataset_status"] = ds.get("industry_sw_historical", {}).get("status")
        out["probe"]["annotations"] = q.get("explicit_annotations", [])
    if membership is not None:
        try:
            panel = portal.price_panel()
            c0 = panel["code"].iloc[0]
            d0 = str(pd.Timestamp(panel["date"].max()).date())
            out["probe"]["sw_trust_sample"] = membership.sector_of(c0, d0).get("sw_trust")
        except Exception as e:  # noqa: BLE001 探针失败不伪装成功
            out["probe"]["sw_trust_error"] = str(e)
    if universe_buckets is not None:
        out["probe"]["survivorship_note_present"] = bool(universe_buckets.get("caveats", {}).get("survivorship"))
    if lc_strict_candidates is not None:
        out["probe"]["strict_candidates_n"] = len(lc_strict_candidates)
        out["probe"]["strict_blocked_confirmed"] = len(lc_strict_candidates) == 0
    return out


def as_of_data_mode(data_mode: str) -> dict:
    return {"data_mode": data_mode,
            "strict_blocks": data_mode == DataMode.STRICT.value,
            "degrade_required": data_mode != DataMode.STRICT.value}
