# -*- coding: utf-8 -*-
"""独立复核缺陷修复的行为锁定测试(评审A确认这些行为此前"未被测试锁定")。
覆盖: FV死分支复活 / REBOUND_ACTIVE过期 / FV STRICT闸门 / LC平坦底部trend60==0 / 陈旧越界守卫。"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from cycle_system.ashare_v2.fast_v import FastVEngine           # noqa: E402
from cycle_system.ashare_v2.long_cycle import LongCycleEngine   # noqa: E402
from cycle_system.ashare_v2.schemas import FastVState, LongCycleState  # noqa: E402


class DuckPortal:
    def __init__(self, df):
        self._df = df

    def price_panel(self):
        return self._df


def _panel(px_by_code, start="2024-01-02"):
    rows = []
    for code, px in px_by_code.items():
        dates = pd.bdate_range(start, periods=len(px))
        for d, p in zip(dates, px):
            rows.append({"date": d, "code": code, "close_adj": float(p),
                         "amount": 1e8, "sector": "IND_T"})
    return pd.DataFrame(rows)


def _accel_crash():
    """230日平台(建250日滚动基线)→10日缓跌→10日加速跌(-4%/日, accel≤-0.03+vol_z≥1.5触发SHOCK)。"""
    px = [100.0] * 230
    px += list(100.0 * 0.99 ** np.arange(1, 11))
    last = px[-1]
    px += list(last * 0.96 ** np.arange(1, 11))
    return px


def _shock_rebound_then_new_low():
    """恐慌下跌→企稳→候选(REVERSAL_CANDIDATE)→次日再创新低(§3.3失败回退)。"""
    px = _accel_crash()
    low = px[-1]
    px += [low * 1.001, low * 1.002, low * 1.002, low * 1.0025]   # 企稳4日 → CANDIDATE
    confirm_i = len(px) - 1
    px += [low * 0.92]                                       # 次日跌破运行低点 → 应回退BOTTOM_SEARCH
    return px, confirm_i


def test_fv_new_low_fallback_branch_is_live():
    """缺陷修复锁: REVERSAL_CANDIDATE 再创新低必须回 BOTTOM_SEARCH(此前死代码恒不触发)。"""
    px, ci = _shock_rebound_then_new_low()
    fv = FastVEngine(DuckPortal(_panel({"600900": px})))
    fv.build()
    dates = pd.bdate_range("2024-01-02", periods=len(px))
    st_at = fv.state_at("600900", str(dates[ci].date()))
    assert st_at in (FastVState.REVERSAL_CANDIDATE.value, FastVState.ENTRY_CONFIRMED.value), \
        f"反弹确认日应处于候选态, 实得{st_at}"
    st_next = fv.state_at("600900", str(dates[ci + 1].date()))
    assert st_next == FastVState.BOTTOM_SEARCH.value, \
        f"再创新低必须回退BOTTOM_SEARCH(§3.3失败回退), 实得{st_next}"


def _shock_stabilize_rebound(tail_days=45):
    """恐慌下跌→企稳→反弹确认→缓涨(不创新低不衰竭), 用于过期/闸门测试。"""
    px = _accel_crash()
    low = px[-1]
    px += [low * 1.001, low * 1.002, low * 1.002, low * 1.0025]
    px += [low * 1.05, low * 1.07]                            # 反弹确认(>3%且上穿MA5)
    px += [low * (1.07 + 0.0004 * k) for k in range(1, tail_days)]
    return px


def test_fv_rebound_active_expires():
    """缺陷修复锁: 反弹链非吸收态 — 超过FV_EXPIRE_D(自入场确认)必须EXPIRED/重置。"""
    from cycle_system.ashare_v2 import config as C
    px = _shock_stabilize_rebound(C.FV_EXPIRE_D + 10)
    fv = FastVEngine(DuckPortal(_panel({"600901": px})))
    fv.build()
    dates = pd.bdate_range("2024-01-02", periods=len(px))
    mid = fv.state_at("600901", str(dates[len(px) - C.FV_EXPIRE_D - 3].date()))
    assert mid in (FastVState.ENTRY_CONFIRMED.value, FastVState.REBOUND_ACTIVE.value), \
        f"中段应处于反弹链, 实得{mid}"
    st_end = fv.state_at("600901", str(dates[-1].date()))
    assert st_end in (FastVState.EXPIRED.value, FastVState.NO_SETUP.value,
                      FastVState.REVERSAL_FAILED.value), \
        f"反弹链不得无限滞留(实测此前age可达190): 末日态={st_end}"


def test_fv_strict_gate_blocks():
    """缺陷修复锁: STRICT 下 Fast-V 同样不得产出正式信号(此前 data_mode 存而不用)。"""
    px = _shock_stabilize_rebound(6)
    fv = FastVEngine(DuckPortal(_panel({"600902": px})), data_mode="strict")
    fv.build()
    dates = pd.bdate_range("2024-01-02", periods=len(px))
    fv_r = FastVEngine(DuckPortal(_panel({"600902": px})), data_mode="research")
    fv_r.build()
    day = str(dates[len(px) - 5].date())                     # 反弹确认窗口内
    assert fv_r.state_at("600902", day) in (FastVState.REVERSAL_CANDIDATE.value,
                                            FastVState.ENTRY_CONFIRMED.value), "research态应处于候选链"
    assert len(fv_r.candidates(day)) > 0, "research模式该日应有FV候选(证明闸门测的是真路径)"
    assert fv.candidates(day) == []
    assert any(r["gate"] == "STRICT_FAST_V" for r in fv.strict_gate_report)


def test_lc_flat_bottom_trend60_zero_allows_forming():
    """缺陷修复锁: trend60==0.0(完全走平)满足>-0.05, 不再被`or -9`哨兵误判。"""
    lc = LongCycleEngine.__new__(LongCycleEngine)            # 只测_step, 免build
    lc.data_mode = "research"
    row = pd.Series({"rp250": 0.30, "trend250": -0.02, "below_long": False, "ma250": 90.0,
                     "px": 50.0, "no_new_low_60": True, "base_rising": True,
                     "slope250_down": False, "ma60": 52.0, "ma120": 55.0,
                     "trend60": 0.0, "min60": 49.0, "dd500": -0.45, "mom12": -0.1})
    mem = {"imp": 0, "ref": None, "above_long": False}
    st = lc._step("IND", "2024-06-03", row, LongCycleState.BOTTOM_WATCH.value, mem)
    assert st == LongCycleState.BOTTOM_FORMING.value, f"平坦底部trend60=0应可进入FORMING, 实得{st}"
    assert mem["ref"] == 49.0


def test_lc_stale_query_beyond_truncation_raises():
    """缺陷修复锁: 截断build后越界查询必须raise(此前静默返回陈旧态/0候选)。"""
    px = list(np.linspace(100, 50, 300)) + list(np.linspace(50, 55, 60))
    portal = DuckPortal(_panel({"600903": px}))
    builder = type("B", (), {"index_for": lambda self, ind, d: {
        "series": portal._df["close_adj"], "market_series": portal._df["close_adj"],
        "status": "PROXY"}})()
    dates = pd.bdate_range("2024-01-02", periods=len(px))
    lc = LongCycleEngine(portal, builder, type("M", (), {"sector_of": lambda s, c, d: {
        "sw_trust": "UNKNOWN"}})(), data_mode="research")
    cut = str(dates[200].date())
    lc.build(cut)
    with pytest.raises(RuntimeError):
        lc.industry_state("IND_T", str(dates[250].date()))


# ── 冻结退出判据锁定(评审B/红线: 修复工程错误不得顺改预测规则) ─────────
def test_frozen_exit_criteria_locked():
    from cycle_system.ashare_v2 import exit_manager as EM
    assert EM.REDUCE_FRACTION == 0.5, "REDUCE_FRACTION属冻结退出判据, 任何改动=新独立版本"
    assert (EM.LC_WEAK_REDUCE, EM.LC_WEAK_EXIT) == (-0.10, -0.20)


def test_strict_blocked_is_probe_driven():
    """缺陷修复锁: strict_blocked不再恒True — 有报告时按双引擎probe判定, 无报告fail-closed。"""
    from cycle_system.ashare_v2 import strict_gate as SG
    assert SG.strict_blocked() is True                        # 无证据: fail-closed
    rep_ok = {"strict_formal_signals_possible": False, "probe": {"strict_blocked_confirmed": True}}
    rep_bad = {"strict_formal_signals_possible": False, "probe": {"strict_blocked_confirmed": False}}
    assert SG.strict_blocked(rep_ok) is True
    assert SG.strict_blocked(rep_bad) is False                # 探针未确认→不得虚报"已复核"
    rep = SG.evaluate()                                        # 无探针→ASSERTED
    assert all(r["status"].startswith("ASSERTED") for r in rep["gates"])
    rep2 = SG.evaluate(lc_strict_candidates=[], fv_strict_candidates=[])
    assert rep2["probe"]["fv_strict_blocked_confirmed"] is True
    assert rep2["probe"]["strict_blocked_confirmed"] is True
    assert rep2["gates"][0]["status"] == "BLOCKING"
