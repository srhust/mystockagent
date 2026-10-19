# -*- coding: utf-8 -*-
"""R1状态机合成测试(裁定B-8): 正常恢复/再次锁定/防满仓反噬/冷却独立性/守恒前置。
纯状态机层, 不触碰任何历史数据(参数预注册, 历史只验迁移)。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "cycle_system", "ashare_v2", "tools"))

from r1_state_machine import (VERSION, COOLDOWN_DAYS, PROBE_DAYS, PROBE_BUDGET_SCALE,
                              LOCK_TRIGGER, MARKET_OBS_MIN, init_state, step)


def _obs(day, dd_p=-0.05, dd_g=-0.05, br=0.5, nav=10000.0, exp=0.5, liq=True):
    return {"day": day, "dd_period": dd_p, "dd_global": dd_g, "breadth5": br,
            "nav": nav, "pos_exposure": exp, "lockout_liquidated": liq}


def _run(st, obs_list):
    trace = []
    for o in obs_list:
        st, act, log = step(st, o)
        trace.append((st["state"], act))
    return st, trace


def test_version_preregistered():
    assert VERSION == "R1.0-PREREG"


def test_trigger_lockout_then_full_recovery_no_deadlock():
    """裁定核心场景1(活性): 触发→锁定→清仓→冷却(NAV恒平也不阻碍)→市场观测→试探→ACTIVE。"""
    st = init_state()
    st, act, log = step(st, _obs("d1", dd_p=LOCK_TRIGGER - 0.001))
    assert st["state"] == "LOCKOUT" and act["force_liquidate"] and not act["allow_new"]
    # 清仓完成
    st, act, _ = step(st, _obs("d2", dd_p=LOCK_TRIGGER - 0.001))   # liquidated=True→COOLDOWN
    assert st["state"] == "COOLDOWN" and not act["force_liquidate"]
    # 冷却20日: NAV恒平(零敞口)+breadth不足 → 只延长, 不复活死锁条件
    seq = [_obs(f"c{i}", dd_p=LOCK_TRIGGER - 0.001, dd_g=LOCK_TRIGGER - 0.001, br=0.30, nav=8000.0)
           for i in range(COOLDOWN_DAYS)]
    st, trace = _run(st, seq)
    assert st["state"] == "COOLDOWN", f"冷却期满但市场观测不足应延长COOLDOWN: {st}"
    assert all(a["budget_scale"] == 0.0 for _, a in trace), "COOLDOWN全程禁止新增风险"
    # 市场观测恢复 → PROBE(受限预算) → 40日 → ACTIVE
    # (runner锚切换契约: 进PROBE起dd_period按本期锚重算, 不再携带锁定期旧值)
    st, act, _ = step(st, _obs("p0", dd_p=-0.05, br=MARKET_OBS_MIN, nav=8000.0))
    assert st["state"] == "PROBE_REENTRY" and act["budget_scale"] == PROBE_BUDGET_SCALE
    st, trace = _run(st, [_obs(f"q{i}", dd_p=-0.05, br=0.6, nav=8000.0 + i)
                          for i in range(PROBE_DAYS)])
    assert st["state"] == "ACTIVE", "40日试探完好必须回ACTIVE全额预算(死锁已破)"
    assert all(a["budget_scale"] == PROBE_BUDGET_SCALE for _, a in trace[:-1])


def test_probe_failure_relocks_never_direct_full_reentry():
    """裁定场景2(防满仓反噬): 试探期-8%立即再LOCKOUT; 任何回到ACTIVE的路径必经COOLDOWN+PROBE。"""
    st = init_state()
    st, _, _ = step(st, _obs("d1", dd_p=LOCK_TRIGGER - 0.001))
    st, _, _ = step(st, _obs("d2", dd_p=LOCK_TRIGGER - 0.001))          # →COOLDOWN
    st, _ = (_run(st, [_obs(f"c{i}", dd_p=LOCK_TRIGGER - 0.001, br=MARKET_OBS_MIN, nav=8000.0)
                       for i in range(COOLDOWN_DAYS)]))[0], None
    st, act, _ = step(st, _obs("p", dd_p=-0.05, br=0.5, nav=8000.0))
    assert st["state"] == "PROBE_REENTRY"
    st, act, log = step(st, _obs("f", dd_p=-0.05, br=0.5, nav=8000.0 * (1 - 0.085)))
    assert st["state"] == "LOCKOUT" and act["force_liquidate"] and "FAIL" in log["rule"]
    # 再入场必须再次完整走 LOCKOUT→COOLDOWN→PROBE: 检查无ACTIVE捷径
    seen = set()
    st2 = st
    for i in range(COOLDOWN_DAYS + PROBE_DAYS + 5):
        st2, act2, _ = step(st2, _obs(f"r{i}", dd_p=-0.10, br=0.55, nav=7300.0))
        seen.add(st2["state"])
        if st2["state"] == "ACTIVE":
            break
    assert "COOLDOWN" in seen and "PROBE_REENTRY" in seen


def test_global_record_never_reset():
    """B-3: dd_global随状态推进持续上报且劣于dd_period记录域(峰值不重置由runner层保证, 此处验字段存在)。"""
    st = init_state()
    _, act, log = step(st, _obs("d1", dd_p=-0.21, dd_g=-0.35))
    assert log["dd_global"] == -0.35 and log["dd_period"] == -0.21


def test_liquidation_pending_holds_lockout():
    """LOCKOUT当日清仓未完成(如跌停)→停留LOCKOUT继续强平尝试, 不进冷却计数。"""
    st = init_state()
    st, act, _ = step(st, _obs("d1", dd_p=LOCK_TRIGGER - 0.001))
    st, act, log = step(st, _obs("d2", dd_p=LOCK_TRIGGER - 0.001, liq=False))
    assert st["state"] == "LOCKOUT" and act["force_liquidate"] and "await" in log["rule"]


def test_no_bypass_when_active_steady():
    st = init_state()
    st, act, _ = step(st, _obs("d1", dd_p=-0.10, br=0.5))
    assert st["state"] == "ACTIVE" and act["budget_scale"] == 1.0 and act["allow_new"]
