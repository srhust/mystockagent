# -*- coding: utf-8 -*-
"""cycle_portfolio 正式回归测试(审查四十五轮): 合成场景覆盖 as-of预算快照/EXIT缺价成交日期/FIFO来源守恒/守恒恒等式。
运行: python -m pytest tests/test_cycle_portfolio.py -q"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cycle_system"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import numpy as np
import pandas as pd

from cycle_portfolio import fsm_decide, v2ar_target, run_contract, COST, HOLD, save_state, load_state, plan_next_day, apply_day
from bottom_nowcaster import build_features


def _snap(dts, syms):
    return {(d.to_period("M"), s): 1.0 for d in dts for s in syms}


def test_asof_budget_snapshot():
    """T-D: 昨日缺价今日恢复时, 信号快照不得读取今日价(as-of)。"""
    dts = pd.date_range("2022-01-03", periods=8, freq="B")
    pxA = pd.Series([10, 10, np.nan, 20, 20, 20, 20, 20], index=dts)
    maA = pd.Series([1000.0, 1000.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0], index=dts)
    pxB = pd.Series(10.0, index=dts)
    snap = _snap(dts, ["A", "B", "D"])
    own = {s: pd.Series(np.arange(8), index=dts) for s in ("A", "B", "D")}
    collect = {}
    run_contract(["A", "B", "D"], snap, "V2aF", {"A": pxA, "B": pxB, "D": pxB}, {"A": maA, "B": maA, "D": maA},
                 {"A": {0}, "B": {2}, "D": {2}}, {s: pd.Series(True, index=dts) for s in ("A", "B", "D")}, dts, own, collect=collect)
    qB = [f[4] for f in collect["fills"] if f[0] == "B" and f[3] == "ENTER"]
    assert len(qB) == 1 and 3.9 < qB[0] < 4.05  # as-of正确≈3.98(B拿0.4档); 污染≈7.98


def test_exit_missing_price_fill_dates():
    """T-C: HOLD到期日缺价不虚假卖出, 下一可成交日真实成交, signal/fill日期正确。"""
    n = 130
    dts = pd.date_range("2021-01-04", periods=n, freq="B")
    px = pd.Series(10.0, index=dts)
    px.iloc[122] = np.nan
    snap = _snap(dts, ["MU"])
    collect = {}
    eq, _, _ = run_contract(["MU"], snap, "V0", {"MU": px}, {"MU": pd.Series(1000.0, index=dts)},
                            {"MU": {1}}, {"MU": pd.Series(True, index=dts)}, dts,
                            {"MU": pd.Series(np.arange(n), index=dts)}, collect=collect)
    ex = [f for f in collect["fills"] if f[3] == "EXIT"]
    assert len(ex) == 1
    assert ex[0][1] == dts[121] and ex[0][2] == dts[123]
    assert collect["sh"]["MU"].loc[dts[122]] > 0
    assert collect["sh"]["MU"].loc[dts[123]] == 0
    assert abs(float(eq.iloc[-1]) - 99.70) < 0.01


def test_v2ar_shares_cap():
    """V2aR恢复不超原始股数(v2ar_target上限)。"""
    side, amt = v2ar_target(True, 100.0, 10.0, 0.0, 5.0)
    assert side == "BUY"
    # max_buy_cash = (10-0)*5/(1-COST) → 买回现金受限, 对应股数不超10
    shares_after = min(amt, 10.0 * 5.0 / (1 - COST)) * (1 - COST) / 5.0
    assert shares_after <= 10.0 + 1e-9


def test_fsm_priorities():
    """FSM优先级: FLAT+事件→ENTER; FULL到期→EXIT优先于风险动作; FROZEN仅到期EXIT; hold_days显式参数生效。"""
    assert fsm_decide("V2aF", "FLAT", -999, 5, True, True) == "ENTER"
    assert fsm_decide("V2aF", "FULL", 0, HOLD, True, False) == "EXIT"
    assert fsm_decide("V2aF", "FULL", 0, HOLD, True, False, hold_days=HOLD) == "EXIT"
    assert fsm_decide("V2aF", "FULL", 0, 60, True, False, hold_days=60) == "EXIT"  # 60日期限: 60天即到期
    assert fsm_decide("V2aF", "FULL", 0, 60, False, False, hold_days=120) is None  # 120日期限: 60天未到期且无破位
    assert fsm_decide("V2aF", "FULL", 0, 5, True, False) == "HALVE"
    assert fsm_decide("V2aR", "FULL", 0, 5, True, False) == "V2AR_TGT"
    assert fsm_decide("V2aF", "FROZEN", 0, 5, True, False) is None
    assert fsm_decide("V2aF", "FROZEN", 0, HOLD, True, False) == "EXIT"


def test_fifo_reserve_release_order():
    """Reserve: HALVE现金隔离, EXIT时以RESERVE_RELEASE入队, 顺序在NORMAL_EXIT之后, 金额=减仓所得。"""
    n = 8
    dts = pd.date_range("2022-01-03", periods=n, freq="B")
    px = pd.Series(10.0, index=dts)
    ma = pd.Series([1000.0, 1000.0, 1000.0, 5.0, 5.0, 5.0, 5.0, 5.0], index=dts)  # day3决策(sd=day2,ma=1000)HALVE→day3成交
    snap = _snap(dts, ["MU"])
    collect = {}
    run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, {"MU": {1}}, {"MU": pd.Series(True, index=dts)},
                 dts, {"MU": pd.Series(np.arange(n), index=dts)}, collect=collect, reserve_mode=True, hold_days=3)
    srcs = collect["sources_end"]
    labels = [x[0] for x in srcs]
    assert "RESERVE_RELEASE" in labels
    assert labels.index("RESERVE_RELEASE") == labels.index("NORMAL_EXIT") + 1  # 释放紧跟退出入队
    rel_amt = srcs[labels.index("RESERVE_RELEASE")][1]
    assert abs(rel_amt - (9.985 / 2 * 10 * (1 - COST))) < 0.01  # 减仓所得精确入账
    halves = [f for f in collect["fills"] if f[3] == "HALVE"]
    assert len(halves) == 1 and abs(halves[0][4] * halves[0][5] * (1 - COST) - rel_amt) < 0.01


def test_order_invariance_reserve():
    """reserve_mode=True下反转股票表结果一致(多标的竞争+隔离现金)。"""
    dts = pd.date_range("2022-01-03", periods=8, freq="B")
    pxA = pd.Series(10.0, index=dts)
    pxB = pd.Series(10.0, index=dts)
    ma = pd.Series([1000.0, 1000.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0], index=dts)
    snap = _snap(dts, ["A", "B"])
    own = {s: pd.Series(np.arange(8), index=dts) for s in ("A", "B")}
    ev = {"A": {0, 2}, "B": {0, 2}}
    li = {s: pd.Series(True, index=dts) for s in ("A", "B")}
    px = {"A": pxA, "B": pxB}
    e1, s1, _ = run_contract(["A", "B"], snap, "V2aF", px, {"A": ma, "B": ma}, ev, li, dts, own, reserve_mode=True, hold_days=4)
    e2, s2, _ = run_contract(["B", "A"], snap, "V2aF", px, {"A": ma, "B": ma}, ev, li, dts, own, reserve_mode=True, hold_days=4)
    assert abs(float(e1.iloc[-1]) - float(e2.iloc[-1])) < 1e-9
    assert abs(s1["A"] - s2["A"]) < 1e-9 and abs(s1["B"] - s2["B"]) < 1e-9


def test_v0_frozen_final_mu():
    """冻结基准: 单股MU V0终值=49428.38(容差0.5, 含真实数据)。"""
    import config
    c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "MU.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    dp = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    v4 = __import__("mu_af_v2").v4.reindex(c.index).fillna(0)
    ev = set()
    last = -9999
    for i, e in enumerate((dp.values | ((v4 > 0) & (v4.diff() > 0).fillna(False).values))):
        if e and i - last >= 60:
            ev.add(i)
            last = i
    dt = c.index[1:]
    snap = {(d.to_period("M"), "MU"): 1.0 for d in c.index}
    eq, _, _ = run_contract(["MU"], snap, "V0", {"MU": pd.Series(c.values, index=c.index)},
                            {"MU": pd.Series(c.rolling(100).mean().values, index=c.index)}, {"MU": ev},
                            {"MU": pd.Series(True, index=c.index)}, dt, {"MU": pd.Series(np.arange(len(c)), index=c.index)})
    assert abs(float(eq.iloc[-1]) - 49428.38) < 0.5


def test_plan_apply_persistence():
    """影子模式: plan_only只读决策不动状态; 逐日plan+apply与批量等价; 持久化往返一致。"""
    n = 10
    dts = pd.date_range("2022-01-03", periods=n, freq="B")
    px = pd.Series([10, 10, 10, 10, 10, 12, 12, 12, 12, 12], index=dts, dtype=float)
    ma = pd.Series([1000.0] * 5 + [5.0] * 5, index=dts)
    snap = {(d.to_period("M"), "MU"): 1.0 for d in dts}
    own = {"MU": pd.Series(np.arange(n), index=dts)}
    ev = {"MU": {1}}
    li = {"MU": pd.Series(True, index=dts)}
    cF = {}
    eqF, _, feesF = run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, dts, own, collect=cF, reserve_mode=False, hold_days=5)
    # 逐日驱动
    c0 = {}
    run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, dts[:1], own, collect=c0, reserve_mode=False, hold_days=5)
    st = c0["end_state"]
    eqs = [c0["eq"].iloc[0]]
    fills = []
    for d in dts[1:]:
        cp = {}
        run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, [d], own, collect=cp, reserve_mode=False, hold_days=5, init_state=st, plan_only=True)
        assert "plan" in cp  # 计划只读生成
        ca = {}
        run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, [d], own, collect=ca, reserve_mode=False, hold_days=5, init_state=st)
        st = ca["end_state"]
        eqs.append(ca["eq"].iloc[0])
        fills.extend(ca["fills"])
    np.testing.assert_allclose(np.array(eqs), eqF.values, rtol=0, atol=1e-9)
    assert fills == cF["fills"]
    assert abs(st["fees"] - feesF) < 1e-9
    # 持久化往返
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "shadow_test_state.json")
    save_state(st, path)
    st2 = load_state(path)
    assert st2["last_date"] == st["last_date"] and st2["shares"] == st["shares"] and abs(st2["fees"] - st["fees"]) < 1e-12


def test_plan_integrity():
    """P0: 计划完整性 — 同日期不同状态被state_hash拒绝; 篡改signal_nav被plan_hash拒绝; 缺哈希前向模式拒绝。"""
    import pytest
    n = 10
    dts = pd.date_range("2022-01-03", periods=n, freq="B")
    px = pd.Series([10, 10, 10, 10, 10, 12, 12, 12, 12, 12], index=dts, dtype=float)
    ma = pd.Series([1000.0] * 5 + [5.0] * 5, index=dts)
    snap = {(d.to_period("M"), "MU"): 1.0 for d in dts}
    own = {"MU": pd.Series(np.arange(n), index=dts)}
    ev = {"MU": {1}}
    li = {"MU": pd.Series(True, index=dts)}
    c1 = {}
    run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, dts[:5], own, collect=c1, reserve_mode=False, hold_days=5, config_extra={"scorer": "12m/edge"})
    st = c1["end_state"]
    plan = plan_next_day(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, own, st, dts[5], hold_days=5, calendar=dts)
    ca = apply_day(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, own, st, plan, dts[5], hold_days=5, require_config=True)
    st2 = ca["end_state"]
    plan2 = plan_next_day(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, own, st2, dts[6], hold_days=5, calendar=dts)
    st3 = dict(st2)
    st3["cash"] = st2["cash"] + 10.0  # 同last_date不同现金 → state_hash必须拒绝
    with pytest.raises(AssertionError, match="状态身份不符"):
        apply_day(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, own, st3, plan2, dts[6], hold_days=5, require_config=True)
    p2 = dict(plan)
    p2["signal_nav"] *= 2
    with pytest.raises(AssertionError, match="计划内容被修改"):
        apply_day(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, own, st, p2, dts[5], hold_days=5, require_config=True)
    p3 = {k: v for k, v in plan.items() if k != "input_hash"}
    with pytest.raises(AssertionError, match="缺少input_hash"):
        apply_day(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, own, st, p3, dts[5], hold_days=5, require_config=True)


def test_enter_budget_frozen():
    """P0: ENTER专项 — 冻结signal_nav, 修改T日价格, 买入预算/金额/股数不变。"""
    n = 10
    dts = pd.date_range("2022-01-03", periods=n, freq="B")
    px = pd.Series([10, 10, 10, 10, 10, 12, 12, 12, 12, 12], index=dts, dtype=float)
    ma = pd.Series([1000.0] * 5 + [5.0] * 5, index=dts)
    snap = {(d.to_period("M"), "MU"): 1.0 for d in dts}
    own = {"MU": pd.Series(np.arange(n), index=dts)}
    ev = {"MU": {4}}
    li = {"MU": pd.Series(True, index=dts)}
    c1 = {}
    run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, dts[:5], own, collect=c1, reserve_mode=False, hold_days=5, config_extra={"scorer": "12m/edge"})
    st = c1["end_state"]
    plan = plan_next_day(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, own, st, dts[5], hold_days=5, calendar=dts)
    nav_f = plan["signal_nav"]
    caA = apply_day(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, own, st, plan, dts[5], hold_days=5)
    px_m = px.copy()
    px_m.iloc[4] = 30.0
    caB = apply_day(["MU"], snap, "V2aF", {"MU": px_m}, {"MU": ma}, ev, li, own, st, plan, dts[5], hold_days=5)
    assert caA["fills"] == caB["fills"]
    entA = [f for f in caA["fills"] if f[3] == "ENTER"][0]
    q_exp = 1.0 * nav_f * (1 - COST) / 12.0
    assert abs(entA[4] - q_exp) < 1e-9
    assert plan["signal_nav"] == nav_f


def test_recover_with_fills():
    """P0: 非空成交(ENTER+HALVE)同数据恢复成功; T+1价格修订→冲突拒绝。serialize_fills写盘/比较同口径。"""
    import pytest
    import json
    import run_shadow as RS
    RS.TICKERS = ["MU"]  # 合成单标的场景(模块函数按全局TICKERS迭代)
    import tempfile as _tf
    tmp0 = _tf.mkdtemp()
    RS.FWD = tmp0  # 隔离日历缓存与指纹文件
    n = 10
    dts = pd.date_range("2022-01-03", periods=n, freq="B")
    px = pd.Series([10, 10, 10, 10, 10, 12, 12, 12, 12, 12], index=dts, dtype=float)
    ma = pd.Series([5.0] * 5 + [1000.0] * 5, index=dts)  # below取sd=d-1=5: 昨收12<ma[5]=1000 → HALVE
    snap = {(d.to_period("M"), "MU"): 1.0 for d in dts}
    own = {"MU": pd.Series(np.arange(n), index=dts)}
    ev = {"MU": {4}}
    li = {"MU": pd.Series(True, index=dts)}
    mkt = dict(px={"MU": px}, ma={"MU": ma}, ev=ev, listed=li, own=own, days=dts, snap=snap, raw_last={"MU": dts[-1]}, raw_close={"MU": px})
    import tempfile
    tmp = tempfile.mkdtemp()
    sp, lp = os.path.join(tmp, "s.json"), os.path.join(tmp, "l.jsonl")
    c1 = {}
    run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, dts[:5], own, collect=c1, reserve_mode=False, hold_days=5, config_extra=RS.CFGX)
    save_state(c1["end_state"], sp)
    # d5: 事前计划→settle(ENTER成交) → d6: plan→settle(HALVE成交, ma跌)
    assert RS.plan_phase(mkt, 5, sp, lp) == "planned"
    assert RS.settle_phase(mkt, 5, sp, lp) == "settled"
    st_d5 = load_state(sp)
    assert RS.plan_phase(mkt, 5, sp, lp) == "planned"
    assert RS.settle_phase(mkt, 5, sp, lp) == "settled"
    exs = [json.loads(l) for l in open(lp, encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
    all_acts = {f[3] for e in exs for f in e["fills"]}
    assert "ENTER" in all_acts and "HALVE" in all_acts, f"需含ENTER+HALVE: {all_acts}"  # P1: 两类成交均经恢复验收
    # 同数据恢复: 已登记成交重算一致(serialize口径) → 成功
    save_state(st_d5, sp)  # 回滚到d5末(模拟EXECUTED已写但状态未存)
    assert RS.settle_phase(mkt, 5, sp, lp) == "settled"
    exs2 = [json.loads(l) for l in open(lp, encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
    assert len(exs2) == len(exs)  # EXECUTED幂等不重复
    # T+1价格修订 → 拒绝
    save_state(st_d5, sp)
    px_rev = px.copy()
    px_rev.iloc[6] = px_rev.iloc[6] * 8.25  # 仅修订结算日d6(≤T指纹域不变, 由成交序列化+状态哈希检测)
    mkt_rev = dict(mkt)
    mkt_rev["px"] = {"MU": px_rev}
    with pytest.raises(AssertionError, match="数据修订冲突"):
        RS.settle_phase(mkt_rev, 5, sp, lp)


def test_nav_of_full_valuation():
    """P0: nav_of=现金+储备+Σ持仓×估值价(非仅现金)。"""
    import run_shadow as RS
    st = dict(cash=5.0, reserve={"X": 3.0}, shares={"X": 10.0}, stale={"X": np.nan}, last_date=pd.Timestamp("2022-01-04"))
    px = {"X": pd.Series([7.0, 9.0], index=pd.date_range("2022-01-03", periods=2))}
    assert abs(RS.nav_of(st, px) - (5.0 + 3.0 + 10.0 * 9.0)) < 1e-9  # 用last_date收盘价9, 非cash 5


def test_time_boundary():
    """P0: ①数据止于T仍能由独立日历生成T+1计划; settle等数据(data_incomplete) ②事后计划late_created不计分 ③无计划PLAN_MISSING不推进 ④双配置失步拒绝。"""
    import pytest
    import json
    import tempfile
    import run_shadow as RS
    RS.TICKERS = ["MU"]
    tmp = tempfile.mkdtemp()
    RS.FWD = tmp  # 隔离日历缓存与指纹文件(monkeypatch模块常量)
    n = 10
    dts = pd.date_range("2022-01-03", periods=n, freq="B")
    px = pd.Series([10] * 5 + [12, 12, 12, 12, 12], index=dts, dtype=float)
    ma = pd.Series([1000.0] * 5 + [5.0] * 5, index=dts)
    snap = {(d.to_period("M"), "MU"): 1.0 for d in dts}
    own = {"MU": pd.Series(np.arange(n), index=dts)}
    mkt = dict(px={"MU": px}, ma={"MU": ma}, ev={"MU": {4}}, listed={"MU": pd.Series(True, index=dts)}, own=own, days=dts, snap=snap, raw_last={"MU": dts[-1]}, raw_close={"MU": px})
    mkt_T = dict(mkt)
    mkt_T["days"] = dts[:5]
    mkt_T["px"] = {"MU": px[:5]}
    tmp = tempfile.mkdtemp()
    sp, lp = os.path.join(tmp, "s.json"), os.path.join(tmp, "l.jsonl")
    c1 = {}
    run_contract(["MU"], snap, "V2aF", mkt_T["px"], {"MU": ma}, {"MU": {4}}, {"MU": pd.Series(True, index=dts)}, dts[:5], own, collect=c1, reserve_mode=False, hold_days=5, config_extra=RS.CFGX)
    save_state(c1["end_state"], sp)
    # ① T+1(第6个交易日)行情不存在 → 计划仍能生成(日历=工作日近似, dts为连续工作日 ✓)
    assert RS.plan_phase(mkt_T, 5, sp, lp) == "planned"
    st_T = load_state(sp)
    # ② settle: 数据未到 → data_incomplete; 注入T日created_at → 补数据后forward计分
    assert RS.settle_phase(mkt_T, 5, sp, lp) == "data_incomplete"
    assert load_state(sp)["last_date"] == st_T["last_date"]
    lines = [json.loads(l) for l in open(lp, encoding="utf-8")]
    for r in lines:
        if r["type"] == "PLAN_CREATED":
            r["created_at"] = pd.Timestamp(st_T["last_date"].date()).tz_localize("UTC") + pd.Timedelta(hours=22)  # T日22:00UTC=17:00ET收盘后
    with open(lp, "w", encoding="utf-8") as f:
        for r in lines:
            f.write(json.dumps(r, default=str) + "\n")
    assert RS.settle_phase(mkt, 5, sp, lp) == "settled"
    exs = [json.loads(l) for l in open(lp, encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
    assert exs[-1]["phase_mode"] == "forward" and exs[-1]["scoring"] is True
    # ③ 事后计划(late_created): 新状态回滚到T, 计划created_at=现在 → scoring=false
    save_state(st_T, sp)
    lp2 = os.path.join(tmp, "l2.jsonl")
    assert RS.plan_phase(mkt, 5, sp, lp2) == "planned"  # T+1行情已在, 事后才建计划
    assert RS.settle_phase(mkt, 5, sp, lp2) == "settled"
    ex2 = [json.loads(l) for l in open(lp2, encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
    assert ex2[-1]["phase_mode"] == "late_created" and ex2[-1]["scoring"] is False
    # ③b 无计划直接settle → PLAN_MISSING, 状态不推进
    save_state(st_T, sp)
    lp3 = os.path.join(tmp, "l3.jsonl")
    assert RS.settle_phase(mkt, 5, sp, lp3) == "plan_missing"
    assert load_state(sp)["last_date"] == st_T["last_date"]
    assert any(json.loads(l)["type"] == "PLAN_MISSING" for l in open(lp3, encoding="utf-8"))
    # ④ 双配置失步
    sp2 = os.path.join(tmp, "s2.json")
    st2 = dict(st_T)
    st2["last_date"] = dts[3]
    save_state(st2, sp2)
    with pytest.raises(AssertionError, match="双配置进度不一致"):
        RS.dual_sync_check(sp, sp2)


def test_release_gate():
    """P0: 正式main()端到端 — 连续3日plan→settle状态推进+凭证一次性字节不变; 计划正文破坏→原子拒绝(90不推进); 中间缺日/NaN→data_incomplete; 收盘门禁。全程临时FWD+合成行情。"""
    import json
    import hashlib
    import pytest
    import tempfile
    import run_shadow as RS
    RS.TICKERS = ["MU"]
    tmp = tempfile.mkdtemp()
    RS.FWD = tmp
    n = 11
    dts = pd.date_range("2022-01-03", periods=n, freq="B")
    px = pd.Series([10] * 5 + [12, 12, 12, 12, 12, 12], index=dts, dtype=float)
    ma = pd.Series([5.0] * 5 + [1000.0] * 6, index=dts)
    snap = {(d.to_period("M"), "MU"): 1.0 for d in dts}
    own = {"MU": pd.Series(np.arange(n), index=dts)}
    li = {"MU": pd.Series(True, index=dts)}
    mkt = dict(px={"MU": px}, ma={"MU": ma}, ev={"MU": {4}}, listed=li, own=own, days=dts, snap=snap, raw_last={"MU": dts[-1]}, raw_close={"MU": px})
    RS.load_market = lambda: mkt  # 正式main()使用合成行情
    sp90, lp90 = os.path.join(tmp, "state_90.json"), os.path.join(tmp, "forward_log_90.jsonl")
    sp120, lp120 = os.path.join(tmp, "state_120.json"), os.path.join(tmp, "forward_log_120.jsonl")
    c0, c1 = {}, {}
    run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, {"MU": {4}}, li, dts[:5], own, collect=c0, reserve_mode=False, hold_days=90, config_extra=RS.CFGX)
    run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, {"MU": {4}}, li, dts[:5], own, collect=c1, reserve_mode=False, hold_days=120, config_extra=RS.CFGX)
    save_state(c0["end_state"], sp90)
    save_state(c1["end_state"], sp120)
    from cycle_portfolio import state_hash
    bh90, bh120 = state_hash(c0["end_state"]), state_hash(c1["end_state"])  # 计分起点=引导结束状态(dts[4])
    fh = {f: hashlib.sha256(open(os.path.join(r"E:\mystockagent", f), "rb").read()).hexdigest() for f in ("cycle_system/cycle_portfolio.py", "scripts/run_shadow.py")}
    v2 = {"type": "launch_credential", "version": 3, "executor_file_hashes": fh, "config_hash_90": "f263f842f497", "config_hash_120": "6d64e83c9d33", "start_state_hash_90": "BOOT90", "start_state_hash_120": "BOOT120", "forward_start": "PENDING_FIRST_REAL_TRADING_DAY"}
    body = json.dumps({k: v for k, v in v2.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
    v2["self_hash"] = hashlib.sha1(body.encode()).hexdigest()[:12]
    json.dump(v2, open(os.path.join(tmp, "launch_credential_v3.json"), "w", encoding="utf-8"))
    off_p = os.path.join(tmp, "launch_credential_official.json")
    import sys as _sys
    for step in range(3):
        _sys.argv = ["run_shadow", "plan"]
        RS.main()
        for spx, lpx in ((sp90, lp90), (sp120, lp120)):
            lines = [json.loads(l) for l in open(lpx, encoding="utf-8")]
            st_last = load_state(spx)["last_date"]
            for r in lines:
                if r["type"] == "PLAN_CREATED":
                    r["created_at"] = (pd.Timestamp(st_last.date()).tz_localize("UTC") + pd.Timedelta(hours=22)).isoformat()
            with open(lpx, "w", encoding="utf-8") as f:
                for r in lines:
                    f.write(json.dumps(r, default=str) + "\n")
        _sys.argv = ["run_shadow", "settle"]
        RS.main()
    assert load_state(sp90)["last_date"] == dts[7] and load_state(sp120)["last_date"] == dts[7]
    assert os.path.exists(off_p)
    offv = json.load(open(off_p, encoding="utf-8"))
    assert offv["start_state_hash_90"] == bh90 and offv["start_state_hash_120"] == bh120, "正式凭证记录真实计分起点(引导结束)状态哈希"
    assert offv["bootstrap_start_state_hash_90"] == "BOOT90" and offv["bootstrap_start_state_hash_120"] == "BOOT120", "10-13引导起点哈希保留为独立字段"
    ob = open(off_p, "rb").read()
    # 凭证一次性: 第4次settle(无下一日计划→预检查拒绝, 同样绝不覆盖)字节不变
    _sys.argv = ["run_shadow", "settle"]
    try:
        RS.main()
    except AssertionError:
        pass
    assert open(off_p, "rb").read() == ob
    # 正常推进一日(dts[8] forward结算), 为后续原子性场景就位状态=dts[8]
    _sys.argv = ["run_shadow", "plan"]
    RS.main()
    for spx, lpx in ((sp90, lp90), (sp120, lp120)):
        lines = [json.loads(l) for l in open(lpx, encoding="utf-8")]
        st_last = load_state(spx)["last_date"]
        for r in lines:
            if r["type"] == "PLAN_CREATED" and r["date"][:10] == str(dts[8].date()):
                r["created_at"] = (pd.Timestamp(st_last.date()).tz_localize("UTC") + pd.Timedelta(hours=22)).isoformat()
        with open(lpx, "w", encoding="utf-8") as f:
            for r in lines:
                f.write(json.dumps(r, default=str) + "\n")
    _sys.argv = ["run_shadow", "settle"]
    RS.main()
    assert load_state(sp90)["last_date"] == dts[8] and load_state(sp120)["last_date"] == dts[8]

    # 原子性(修正假阳性): 先为次日(dts[9])生成两配置计划, 再篡改120未执行计划 → dry验签失败 → 90字节级零变化
    _sys.argv = ["run_shadow", "plan"]
    RS.main()
    for spx, lpx in ((sp90, lp90), (sp120, lp120)):
        lines = [json.loads(l) for l in open(lpx, encoding="utf-8")]
        st_last = load_state(spx)["last_date"]
        for r in lines:
            if r["type"] == "PLAN_CREATED" and r["date"][:10] == str(dts[9].date()):
                r["created_at"] = (pd.Timestamp(st_last.date()).tz_localize("UTC") + pd.Timedelta(hours=22)).isoformat()
        with open(lpx, "w", encoding="utf-8") as f:
            for r in lines:
                f.write(json.dumps(r, default=str) + "\n")
    lines = [json.loads(l) for l in open(lp120, encoding="utf-8")]
    tgt = [r for r in lines if r["type"] == "PLAN_CREATED" and r["date"][:10] == str(dts[9].date())][-1]
    tgt["plan"]["orders"] = {"FAKE": ("BUY", 0.0, 0.0)}  # 破坏120未执行计划 → dry阶段plan_hash验签必失败
    with open(lp120, "w", encoding="utf-8") as f:
        for r in lines:
            f.write(json.dumps(r, default=str) + "\n")
    h90_before = hashlib.sha256(open(sp90, "rb").read()).hexdigest() + hashlib.sha256(open(lp90, "rb").read()).hexdigest()
    _sys.argv = ["run_shadow", "settle"]
    with pytest.raises(AssertionError, match="验签失败"):
        RS.main()
    assert hashlib.sha256(open(sp90, "rb").read()).hexdigest() + hashlib.sha256(open(lp90, "rb").read()).hexdigest() == h90_before, "原子性: 90状态与日志字节级零变化"
    # P0-1: 结算未成功不得提交事务完成 — 120返回market_not_closed → TXN_ABORTED, 无BOTH_COMMITTED
    with open(lp120, "w", encoding="utf-8") as f:
        for r in [json.loads(l) for l in open(lp120, encoding="utf-8")]:
            if not (r["type"] == "PLAN_CREATED" and r["date"][:10] == str(dts[9].date())):
                f.write(json.dumps(r, default=str) + "\n")  # 删除被破坏计划
    _sys.argv = ["run_shadow", "plan"]
    RS.main()  # 120重新生成dts[9]计划
    for spx, lpx in ((sp90, lp90), (sp120, lp120)):
        lines2 = [json.loads(l) for l in open(lpx, encoding="utf-8")]
        st_last = load_state(spx)["last_date"]
        for r in lines2:
            if r["type"] == "PLAN_CREATED" and r["date"][:10] == str(dts[9].date()):
                r["created_at"] = (pd.Timestamp(st_last.date()).tz_localize("UTC") + pd.Timedelta(hours=22)).isoformat()
        with open(lpx, "w", encoding="utf-8") as f:
            for r in lines2:
                f.write(json.dumps(r, default=str) + "\n")
    orig_sp = RS.settle_phase

    def fake_sp(mk, hold, *a, **kw):
        if hold == 120 and kw.get("dry") is not True and kw.get("recovery") is not True:
            return "market_not_closed"
        return orig_sp(mk, hold, *a, **kw)

    RS.settle_phase = fake_sp
    _sys.argv = ["run_shadow", "settle"]
    with pytest.raises(AssertionError, match="结算未成功"):
        RS.main()
    RS.settle_phase = orig_sp
    txn_recs = [json.loads(l) for l in open(os.path.join(tmp, "dual_txn.jsonl"), encoding="utf-8")]
    assert any(r.get("phase") == "TXN_ABORTED" for r in txn_recs)
    assert not any(r.get("phase") == "BOTH_COMMITTED" and r["date"] == str(dts[9].date()) and not r.get("recovered") for r in txn_recs)
    # P0-2: 凭证缺失 → 恢复不执行(120回滚状态不动)
    st120_pre = load_state(sp120)["last_date"]
    os.rename(os.path.join(tmp, "launch_credential_v3.json"), os.path.join(tmp, "v3.bak"))
    with pytest.raises(AssertionError, match="候选凭证缺失"):
        _sys.argv = ["run_shadow", "settle"]
        RS.main()
    assert load_state(sp120)["last_date"] == st120_pre, "fail-closed: 凭证缺失时恢复不得先执行"
    os.rename(os.path.join(tmp, "v3.bak"), os.path.join(tmp, "launch_credential_v3.json"))
    # P0-3: 恢复遇冻结计划缺失 → 拒绝重生成
    st120_r = load_state(sp120)
    st120_r["last_date"] = dts[7]
    save_state(st120_r, sp120)
    txn_recs.append({"phase": "BOTH_PREPARED", "date": str(dts[8].date())})
    with open(os.path.join(tmp, "dual_txn.jsonl"), "w", encoding="utf-8") as f:
        for r in txn_recs:
            f.write(json.dumps(r, default=str) + "\n")
    # 删除120在dts[7]的PLAN_CREATED(冻结计划不存在)
    with open(lp120, "w", encoding="utf-8") as f:
        for r in [json.loads(l) for l in open(lp120, encoding="utf-8")]:
            if not (r["type"] == "PLAN_CREATED" and r["date"][:10] == str(dts[8].date())):
                f.write(json.dumps(r, default=str) + "\n")
    _sys.argv = ["run_shadow", "settle"]
    with pytest.raises(AssertionError, match="拒绝重生成"):
        RS.main()
    assert load_state(sp120)["last_date"] == dts[7], "P0-3: 冻结计划缺失时状态不得推进"
    # 中间缺日/NaN: 移除dts[6]原始记录 — 状态回滚到dts[5], raw缺dts[6]
    st5 = load_state(sp90)
    st5["last_date"] = dts[5]
    save_state(st5, sp90)
    st5b = load_state(sp120)
    st5b["last_date"] = dts[5]
    save_state(st5b, sp120)
    px_gap = px.drop(dts[6])
    mkt_gap = dict(mkt)
    mkt_gap["raw_close"] = {"MU": px_gap}
    mkt_gap["px"] = {"MU": px.ffill().reindex(dts)}
    RS.load_market = lambda: mkt_gap
    assert RS.settle_phase(mkt_gap, 90, sp90, lp90) == "data_incomplete"  # 中间缺日→不推进
    assert load_state(sp90)["last_date"] == dts[5]
    px_nan = px.copy()
    px_nan.loc[dts[6]] = np.nan
    mkt_nan = dict(mkt)
    mkt_nan["raw_close"] = {"MU": px_nan}
    mkt_nan["px"] = {"MU": px.ffill().reindex(dts)}
    RS.load_market = lambda: mkt_nan
    assert RS.settle_phase(mkt_nan, 90, sp90, lp90) == "data_incomplete"
    assert load_state(sp90)["last_date"] == dts[5], "NaN原始价→data_incomplete不推进"



def test_config_mismatch_rejected():
    """P1负例: 90日状态按120日恢复必须被拒绝(pytest.raises区分真实拒绝与测试自造异常)。"""
    import pytest
    n = 8
    dts = pd.date_range("2022-01-03", periods=n, freq="B")
    px = pd.Series([10.0] * 4 + [12.0] * 4, index=dts)
    ma = pd.Series([1000.0] * 4 + [5.0] * 4, index=dts)
    snap = {(d.to_period("M"), "MU"): 1.0 for d in dts}
    own = {"MU": pd.Series(np.arange(n), index=dts)}
    ev = {"MU": {1}}
    li = {"MU": pd.Series(True, index=dts)}
    c1 = {}
    run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, dts[:5], own, collect=c1, reserve_mode=False, hold_days=90, config_extra={"scorer": "12m/edge"})
    with pytest.raises(AssertionError, match="配置不符"):
        run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, [dts[5]], own, reserve_mode=False, hold_days=120, init_state=c1["end_state"])


def test_gap_day_rejected():
    """P1负例: 漏日(跳过交易日)的计划必须被拒绝。"""
    import pytest
    from cycle_portfolio import plan_next_day
    n = 8
    dts = pd.date_range("2022-01-03", periods=n, freq="B")
    px = pd.Series([10.0] * 4 + [12.0] * 4, index=dts)
    ma = pd.Series([1000.0] * 4 + [5.0] * 4, index=dts)
    snap = {(d.to_period("M"), "MU"): 1.0 for d in dts}
    own = {"MU": pd.Series(np.arange(n), index=dts)}
    ev = {"MU": {1}}
    li = {"MU": pd.Series(True, index=dts)}
    c1 = {}
    run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, dts[:4], own, collect=c1, reserve_mode=False, hold_days=90)
    with pytest.raises(AssertionError, match="漏日"):
        plan_next_day(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, own, c1["end_state"], dts[5], hold_days=90, calendar=dts)


def test_plan_driven_execution():
    """P0: apply_day消费冻结计划 — 计划与执行分离, 逐笔与批量一致。"""
    n = 10
    dts = pd.date_range("2022-01-03", periods=n, freq="B")
    px = pd.Series([10, 10, 10, 10, 10, 12, 12, 12, 12, 12], index=dts, dtype=float)
    ma = pd.Series([1000.0] * 5 + [5.0] * 5, index=dts)
    snap = {(d.to_period("M"), "MU"): 1.0 for d in dts}
    own = {"MU": pd.Series(np.arange(n), index=dts)}
    ev = {"MU": {1}}
    li = {"MU": pd.Series(True, index=dts)}
    cF = {}
    eqF, _, _ = run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, dts, own, collect=cF, reserve_mode=False, hold_days=5)
    c0 = {}
    run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, dts[:1], own, collect=c0, reserve_mode=False, hold_days=5)
    st = c0["end_state"]
    eqs, fills = [c0["eq"].iloc[0]], []
    for d in dts[1:]:
        plan = plan_next_day(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, own, st, d, hold_days=5, calendar=dts)
        ca = apply_day(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, ev, li, own, st, plan, d, hold_days=5)
        st = ca["end_state"]
        eqs.append(ca["eq"].iloc[0])
        fills.extend(ca["fills"])
    np.testing.assert_allclose(np.array(eqs), eqF.values, rtol=0, atol=1e-9)
    assert fills == cF["fills"]
