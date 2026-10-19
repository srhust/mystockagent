# -*- coding: utf-8 -*-
"""r1_state_machine — DUAL_CYCLE_V2.R1_RISK_RECOVERY 独立风险状态机(审查裁定①批准, 2026-10-19 20:05)。

背景(R0缺陷实锤, independent/risk_lockout_diagnosis.json): R0的EXCEEDED锁死以"NAV回升至
历史峰值90%"为唯一解冻条件; 锁死伴随清仓→零敞口→NAV恒平(std=0)→条件永不可达→1,436+日
永久锁定, 84,486候选全拒。这是状态机**活性缺陷**, 不是参数问题。

============================  预 注 册 (PREREGISTERED)  ============================
本文件全部规则与参数在接触任何历史数据读数**之前**固定于此。2020-2026年仅用于验证状态
迁移是否正确(活性/防跳变/守恒), 不用于优化冷却时长、试探预算或任何阈值。参数寻优=违规。
版本: R1.0-PREREG(2026-10-19)。任何改动必须升版本并保留R1.0可重放。

状态: ACTIVE | LOCKOUT | COOLDOWN | PROBE_REENTRY   (初始: ACTIVE)
参数(预注册, 不可在对照实验内调):
  LOCK_TRIGGER   : 期内回撤 dd_period <= -0.20 (与R0的20%等值; 域=本期峰值, 见下方"记录与决策分离")
  COOLDOWN_DAYS  : 20 个交易日(LOCKOUT清仓完成后起算; 期满转市场观测判据, 未满不得入场)
  MARKET_OBS     : 市场风险观测量 breadth5 = 全市场20日动量广度(close>ref(close,20)的股票占比,
                   5日均值) >= 0.45; 该量独立于组合自身NAV(零敞口下NAV恒平不构成阻碍——锁死破除点)
  PROBE_DAYS     : 40 个交易日; 期内新增风险预算 scale=0.25(R0正常预算的1/4, 独立受限预算)
  PROBE_FAIL     : 试探期内 dd_probe <= -0.08 或 dd_period <= -0.20 → 立即重新LOCKOUT
  PROBE_SUCCESS  : 满40日且未触发失败 → ACTIVE(恢复全额预算; 期内峰值重锚=恢复时NAV)
  REENTRY_PATH   : LOCKOUT后回到可交易**必须**经过 COOLDOWN→PROBE→ACTIVE; 不存在任何
                   "直接重新满仓"路径(防裁定二极端之一: 触发止损后立刻满仓再挨打)
记录与决策分离(裁定②: 保留全局历史回撤记录, 禁止重置掩盖):
  high_water_global 永不重置, dd_global 每日上报; 但再触发/解冻决策用本期基线 dd_period。
  两者同时进状态日志——掩盖历史记录=违规, 用陈旧全局峰值永封=活性缺陷。
禁止: FV/LC 单独绕过全局状态机(状态机输入只有组合级观测); 调低20%阈值; 重置峰值假装回撤未发生。
====================================================================================

纯函数状态机: step(state, obs) -> (next_state, action, log_row)。无I/O、可重放、可合成测试。
obs = {day, dd_period, dd_global, breadth5, pos_exposure, days_left_in_state, lockout_liquidated}
action = {allow_new: bool, budget_scale: float, force_liquidate: bool}
"""
from __future__ import annotations

VERSION = "R1.0-PREREG"
LOCK_TRIGGER = -0.20
COOLDOWN_DAYS = 20
MARKET_OBS_MIN = 0.45
PROBE_DAYS = 40
PROBE_BUDGET_SCALE = 0.25
PROBE_FAIL_DD = -0.08
STATES = ("ACTIVE", "LOCKOUT", "COOLDOWN", "PROBE_REENTRY")


def init_state() -> dict:
    return {"state": "ACTIVE", "days_in_state": 0, "cooldown_full_days_done": False,
            "probe_start_nav": None, "transitions": []}


def _mk(state: str, days: int) -> dict:
    return {"state": state, "days_in_state": days, "probe_start_nav": None}


def step(st: dict, obs: dict) -> tuple[dict, dict, dict]:
    """一个交易日的状态迁移。obs字段见模块头。返回(新state, action, log_row)。"""
    s, d = st["state"], st["days_in_state"] + 1
    log = {"day": obs.get("day"), "from_state": s, "days_in_state_before": st["days_in_state"],
           "dd_period": obs.get("dd_period"), "dd_global": obs.get("dd_global"),
           "breadth5": obs.get("breadth5"), "pos_exposure": obs.get("pos_exposure")}
    # --- 全状态一致的触发优先级: 期内深回撤(非ACTIVE时仅恶化全局记录) -----------------
    if s == "ACTIVE":
        if obs["dd_period"] <= LOCK_TRIGGER:
            ns = _mk("LOCKOUT", 0)
            log |= {"to_state": "LOCKOUT", "rule": f"dd_period<={LOCK_TRIGGER}"}
            return ns, {"allow_new": False, "budget_scale": 0.0, "force_liquidate": True}, log
        log |= {"to_state": "ACTIVE", "rule": "normal"}
        return _mk("ACTIVE", d), {"allow_new": True, "budget_scale": 1.0, "force_liquidate": False}, log
    if s == "LOCKOUT":
        # 清仓(R1 runner执行force_liquidate一次)后进入冷却计数; 计数不依赖NAV
        if not obs.get("lockout_liquidated"):
            log |= {"to_state": "LOCKOUT", "rule": "await liquidation"}
            return _mk("LOCKOUT", d), {"allow_new": False, "budget_scale": 0.0, "force_liquidate": True}, log
        ns = _mk("COOLDOWN", 0)
        ns["probe_start_nav"] = None
        log |= {"to_state": "COOLDOWN", "rule": f"liquidated -> cooldown {COOLDOWN_DAYS}d (NAV-independent)"}
        return ns, {"allow_new": False, "budget_scale": 0.0, "force_liquidate": False}, log
    if s == "COOLDOWN":
        if d < COOLDOWN_DAYS:
            log |= {"to_state": "COOLDOWN", "rule": f"cooldown {d}/{COOLDOWN_DAYS}"}
            return _mk("COOLDOWN", d), {"allow_new": False, "budget_scale": 0.0, "force_liquidate": False}, log
        if obs["breadth5"] is not None and obs["breadth5"] >= MARKET_OBS_MIN:
            ns = _mk("PROBE_REENTRY", 0)
            ns["probe_start_nav"] = obs.get("nav")
            log |= {"to_state": "PROBE_REENTRY", "rule": f"cooldown done & breadth5>={MARKET_OBS_MIN}"}
            return ns, {"allow_new": True, "budget_scale": PROBE_BUDGET_SCALE, "force_liquidate": False}, log
        log |= {"to_state": "COOLDOWN", "rule": "market obs not satisfied (extension, NAV-agnostic)"}
        return _mk("COOLDOWN", d), {"allow_new": False, "budget_scale": 0.0, "force_liquidate": False}, log
    if s == "PROBE_REENTRY":
        nav0 = st.get("probe_start_nav") or obs.get("nav")
        dd_probe = (obs["nav"] / nav0 - 1.0) if nav0 and obs.get("nav") else 0.0
        if dd_probe <= PROBE_FAIL_DD or obs["dd_period"] <= LOCK_TRIGGER:
            ns = _mk("LOCKOUT", 0)
            log |= {"to_state": "LOCKOUT", "rule": f"probe FAIL dd_probe={dd_probe:.3f}"}
            return ns, {"allow_new": False, "budget_scale": 0.0, "force_liquidate": True}, log
        if d >= PROBE_DAYS:
            log |= {"to_state": "ACTIVE", "rule": f"probe {PROBE_DAYS}d complete & intact"}
            return _mk("ACTIVE", 0), {"allow_new": True, "budget_scale": 1.0, "force_liquidate": False}, log
        log |= {"to_state": "PROBE_REENTRY", "rule": f"probe {d}/{PROBE_DAYS} dd_probe={dd_probe:.3f} scale={PROBE_BUDGET_SCALE}"}
        ns = _mk("PROBE_REENTRY", d)
        ns["probe_start_nav"] = nav0                    # 修复: 续行必须携带试探锚(丢失=dd_probe恒0的假恢复)
        return ns, {"allow_new": True, "budget_scale": PROBE_BUDGET_SCALE, "force_liquidate": False}, log
    raise RuntimeError(f"unknown state {s}")
