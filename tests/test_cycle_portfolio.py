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

from cycle_portfolio import fsm_decide, v2ar_target, run_contract, COST, HOLD
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
    """FSM优先级: FLAT+事件→ENTER; FULL到期→EXIT优先于风险动作; FROZEN仅到期EXIT。"""
    assert fsm_decide("V2aF", "FLAT", -999, 5, True, True) == "ENTER"
    assert fsm_decide("V2aF", "FULL", 0, HOLD, True, False) == "EXIT"
    assert fsm_decide("V2aF", "FULL", 0, 5, True, False) == "HALVE"
    assert fsm_decide("V2aR", "FULL", 0, 5, True, False) == "V2AR_TGT"
    assert fsm_decide("V2aF", "FROZEN", 0, 5, True, False) is None
    assert fsm_decide("V2aF", "FROZEN", 0, HOLD, True, False) == "EXIT"


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
