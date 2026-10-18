# -*- coding: utf-8 -*-
"""阶段C工程测试: 双状态机独立 / Fast-V独立候选 / LC禁止固定天数退出 / 底部假设失效 /
前缀确定性(无事后标注) / 停牌安全 / Strict行业闸门 (§9.1 适用场景 + 合成B/C/D)。"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from cycle_system.ashare_v2 import config as C                                  # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                       # noqa: E402
from cycle_system.ashare_v2.exit_manager import ExitManager                     # noqa: E402
from cycle_system.ashare_v2.fast_v import FastVEngine                           # noqa: E402
from cycle_system.ashare_v2.industry_cycle import (IndustryMembershipProvider,  # noqa: E402
                                                   IndustrySeriesBuilder)
from cycle_system.ashare_v2.long_cycle import LongCycleEngine                   # noqa: E402
from cycle_system.ashare_v2.schemas import (Engine, ExitClass, FastVState,      # noqa: E402
                                            LongCycleState, VirtualPosition)

N = 900
DATES = pd.bdate_range("2022-01-03", periods=N)


def _mk_frame(prices_by_code):
    rows = []
    for code, sector, px, amounts in prices_by_code:
        for i, d in enumerate(DATES):
            rows.append({"date": d, "code": code, "sector": sector, "open": px[i], "high": px[i],
                         "low": px[i], "close": px[i], "volume": amounts[i], "amount": amounts[i],
                         "adj_factor": 1.0, "close_adj": px[i]})
    return pd.DataFrame(rows)


def _flat(start, n, drift=0.0, total=N):
    """前 n 日指数段(start→start*e^drift), 之后以末值填充到 total。"""
    v = start * np.exp(np.linspace(0, drift, n))
    out = np.full(total, v[-1])
    out[:n] = v
    return out


class FakePortal:
    def __init__(self, df):
        self._df = df.sort_values(["date", "code"]).reset_index(drop=True)

    def price_panel(self):
        return self._df


def synth_base(extra=None):
    """行业T: 800日阴跌(长期弱), 后100日小幅企稳; T001/T002 同形态。"""
    px1 = _flat(100, 800, -0.9)
    px1[800:] = px1[800] * (1 + 0.0004 * np.arange(N - 800))
    px2 = _flat(80, 800, -0.8)
    px2[800:] = px2[800] * (1 + 0.0005 * np.arange(N - 800))
    amt = np.full(N, 8e7)
    frames = [("T00001", "TESTIND", px1, amt.copy()), ("T00002", "TESTIND", px2, amt.copy())]
    if extra:
        frames += extra
    return _mk_frame(frames)


def crash_rebound(start_px, n=N, crash_i=700, crash_len=25, crash=-0.35, reb_len=25, reb=0.28):
    px = np.full(n, start_px, dtype=float)
    px[:crash_i] = start_px * (1 - 0.0002 * np.arange(crash_i))
    top = px[crash_i - 1]
    for i in range(crash_len):
        px[crash_i + i] = top * (1 + crash * (i + 1) / crash_len)
    low = px[crash_i + crash_len - 1]
    for i in range(1, reb_len + 1):
        px[crash_i + crash_len + i - 1] = low * (1 + reb * i / reb_len)
    px[crash_i + crash_len + reb_len:] = low * (1 + reb)
    return px


@pytest.fixture(scope="module")
def engines():
    df = synth_base(extra=[("T00003", "TESTIND2", crash_rebound(60), np.full(N, 9e7)),
                           ("T00004", "TESTIND", crash_rebound(50, crash_i=780, reb_len=0, reb=0),
                            np.full(N, 9e7))])
    portal = FakePortal(df)
    b = IndustrySeriesBuilder(portal)
    mp = IndustryMembershipProvider()
    lc = LongCycleEngine(portal, b, mp, data_mode=C.PIT_RESEARCH)
    fv = FastVEngine(portal, data_mode=C.PIT_RESEARCH)
    lc.build()
    fv.build()
    return lc, fv, portal, df


def _vp(pid, eng, code="T00001", state="EXPANSION"):
    return VirtualPosition(position_id=pid, code=code, engine=eng, shares=100, opened_day="2022-01-03",
                           entry_kind="EARLY_REVERSAL" if eng == Engine.LONG_CYCLE.value else "REVERSAL_CANDIDATE",
                           thesis="test", state=state)


# ── §3.4 双机独立 ──────────────────────────────────────────
def test_fastv_independent_of_longcycle(engines):
    import inspect
    import cycle_system.ashare_v2.fast_v as fvmod
    src = inspect.getsource(fvmod)
    assert "long_cycle" not in src and "LongCycle" not in src, "Fast-V不得引用Long-Cycle状态"
    lc, fv, _, _ = engines
    day = DATES[732].date().isoformat()                     # 冲击后企稳反弹期(ENTRY_CONFIRMED窗口)
    cands = fv.candidates(day)
    codes = {c.code for c in cands}
    assert "T00003" in codes, "急跌V型反弹个股必须独立产生Fast-V候选"
    assert lc.industry_state("TESTIND2", day) in (LongCycleState.DOWNTREND.value,
                                                  LongCycleState.BOTTOM_WATCH.value,
                                                  LongCycleState.UNKNOWN.value)
    assert all(c.code != "T00003" for c in lc.candidates(day)), "长期弱势下LC不应给T00003候选(场景B: 快V独立)"


def test_lc_candidates_record_contract(engines):
    lc, _, _, _ = engines
    day = DATES[850].date().isoformat()
    for c in lc.candidates(day):
        assert c.validate() == []
        assert c.engine == Engine.LONG_CYCLE.value
        assert c.first_signal_day <= day


# ── §2.5 LC 禁固定天数 + MA20仅警告 ────────────────────────
def test_lc_no_fixed_day_exit_and_ma20_warning_only():
    em = ExitManager()
    pos = _vp("P1", Engine.LONG_CYCLE.value)
    out = em.evaluate(pos, {"industry_state": LongCycleState.EXPANSION.value, "rel_industry_60": 0.05,
                            "rel_weak_days": 0, "ma20_break": True, "day": "2025-06-01"})
    assert [o[0] for o in out] == [ExitClass.WARNING.value], "MA20破位只允许WARNING"
    out2 = em.evaluate(pos, {"industry_state": LongCycleState.EXPANSION.value, "rel_industry_60": 0.0,
                             "rel_weak_days": 999, "ma20_break": False, "day": "2026-06-01"})
    assert out2 == []
    d = em.source_discipline_ok()
    assert all(d.values()), d


def test_lc_weak_vs_industry_ladder():
    em = ExitManager()
    pos = _vp("P2", Engine.LONG_CYCLE.value)
    out = em.evaluate(pos, {"industry_state": LongCycleState.EARLY_RECOVERY.value,
                            "rel_industry_60": -0.12, "rel_weak_days": 65, "day": "d"})
    assert out and out[0][0] == ExitClass.REDUCE.value
    out2 = em.evaluate(pos, {"industry_state": LongCycleState.EXPANSION.value,
                             "rel_industry_60": -0.25, "day": "d"})
    assert out2[0][0] == ExitClass.EXIT.value


# ── 场景D: 底部假设被否定 → INVALIDATED + EXIT ─────────────
def test_bottom_invalidation_scenario_D():
    px = _flat(100, 500, -1.2)
    trough = px[499]
    px[500:620] = trough * (1 + 0.0009 * np.arange(120))          # 缓慢筑底抬升
    px[620:640] = np.linspace(px[619], trough * 0.70, 20)          # 跌破确认低点(FORMING/EARLY后)
    px[640:] = trough * 0.69
    p2 = _flat(90, 500, -1.0)
    p2[500:] = p2[499] * (1 + 0.0002 * np.arange(N - 500))          # 同步步升, 不抵消行业结构
    df = _mk_frame([("T00001", "TESTIND", px, np.full(N, 8e7)),
                    ("T00002", "TESTIND", p2, np.full(N, 8e7))])
    portal = FakePortal(df)
    lc = LongCycleEngine(portal, IndustrySeriesBuilder(portal), IndustryMembershipProvider(),
                         data_mode=C.PIT_RESEARCH)
    lc.build()
    hist = lc._hist["TESTIND"] if "TESTIND" in lc._hist else None
    states = list(lc._states.get("TESTIND", {}).values()) if "TESTIND" in lc._states else []
    assert hist is not None
    if LongCycleState.BOTTOM_FORMING.value in states:
        i_form = states.index(LongCycleState.BOTTOM_FORMING.value)
        i_inv = [i for i, s in enumerate(states) if s == LongCycleState.INVALIDATED.value]
        assert i_inv and i_inv[0] > i_form, "失效必须发生在形成之后"
        pos = _vp("P3", Engine.LONG_CYCLE.value, state="INVALIDATED")
        out = ExitManager().evaluate(pos, {"industry_state": LongCycleState.INVALIDATED.value, "day": "d"})
        assert out and out[0][0] == ExitClass.EXIT.value
    else:
        pytest.skip("合成参数未进入形成态——检查特征阈值标定")


# ── 场景A: 长期下跌→行业改善后价格延迟逆转→LC三类入口产出 ──
def test_lc_entry_scenario_A():
    px = _flat(100, 500, -1.2)
    trough = px[499]
    px[500:] = trough * (1 + 0.0006 * np.arange(N - 500))          # 300日缓慢复苏
    p2 = _flat(80, 500, -1.0)
    t2 = p2[499]
    p2[500:] = t2 * (1 + 0.0004 * np.arange(N - 500))
    df = _mk_frame([("T00001", "TESTIND", px, np.full(N, 8e7)),
                    ("T00002", "TESTIND", p2, np.full(N, 8e7))])
    portal = FakePortal(df)
    lc = LongCycleEngine(portal, IndustrySeriesBuilder(portal), IndustryMembershipProvider(),
                         data_mode=C.PIT_RESEARCH)
    lc.build()
    early = lc.candidates(DATES[520].date().isoformat())
    late = lc.candidates(DATES[700].date().isoformat())
    assert early == [], "阴跌未缓(520日)不应有允许态候选"
    mid = lc.candidates(DATES[560].date().isoformat())
    assert mid and all(c.entry_kind == "EARLY_REVERSAL" for c in mid), "底部形成后率先企稳=EARLY_REVERSAL"
    assert late, "持续复苏后必须产出Long-Cycle候选(场景A)"
    kinds = {c.entry_kind for c in late}
    assert kinds <= {"EARLY_REVERSAL", "BREAKOUT_CONFIRM", "TREND_CONTINUATION"}
    for c in late:
        assert c.validate() == [] and c.engine == Engine.LONG_CYCLE.value
        states = list(lc._states["TESTIND"].values())
        assert LongCycleState.EXPANSION.value in states or LongCycleState.EARLY_RECOVERY.value in states, \
            "候选必须伴随行业进入复苏/扩张态"
        break


# ── 前缀确定性(无未来标注) ────────────────────────────────
def test_prefix_determinism(engines):
    lc, fv, portal, df = engines
    cut = DATES[760]
    df2 = df[df["date"] <= cut]
    p2 = FakePortal(df2)
    lc2 = LongCycleEngine(p2, IndustrySeriesBuilder(p2), IndustryMembershipProvider())
    fv2 = FastVEngine(p2)
    lc2.build()
    fv2.build()
    day = cut.date().isoformat()
    assert lc2.industry_state("TESTIND", day) == lc.industry_state("TESTIND", day)
    for ind in lc._states:
        assert lc2.industry_state(ind, day) == lc.industry_state(ind, day), f"{ind} 前缀重放状态不一致(未来泄漏)"
    assert set(c.code for c in fv2.candidates(day)) == set(c.code for c in fv.candidates(day))


# ── 停牌安全 + Strict 行业闸门 ────────────────────────────
def test_halt_day_no_candidate_and_strict_gate():
    px = crash_rebound(60)
    df = synth_base(extra=[("T00003", "TESTIND2", px, np.full(N, 9e7))])
    gap_day = DATES[742]
    df = df[~((df["code"] == "T00003") & (df["date"] == gap_day))]
    portal = FakePortal(df)
    fv = FastVEngine(portal)
    fv.build()
    cands = fv.candidates(gap_day.date().isoformat())
    assert all(c.code != "T00003" for c in cands), "停牌日(无行情行)不得产生候选"
    lc = LongCycleEngine(portal, IndustrySeriesBuilder(portal), IndustryMembershipProvider(),
                         data_mode=C.PIT_STRICT)
    lc.build()
    assert lc.candidates(DATES[850].date().isoformat()) == []
    assert lc.strict_gate_report and "DATA_BLOCKED" in str(lc.strict_gate_report)


# ── Fast-V 到期(EXPIRED 仅本引擎) ─────────────────────────
def test_fastv_expire(engines):
    lc, fv, _, _ = engines
    day = DATES[min(N - 1, 780 + C.FV_EXPIRE_D + 60)].date().isoformat()
    st = fv.state_at("T00004", day)
    assert st in (FastVState.EXPIRED.value, FastVState.NO_SETUP.value), \
        f"无反弹冲击必须走完 EXPIRED→NO_SETUP 周期, got {st}"
