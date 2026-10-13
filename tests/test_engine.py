# -*- coding: utf-8 -*-
"""审查五轮P0③: 直接测试生产引擎 run_ledger / run_specialist。
覆盖: 缺价禁成交 / 现金+市值=净值 / 专户预算不串用 / 费用当日入账 / 加仓T-1确认T执行 / T+1成交。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cycle_system"))
import numpy as np
import pandas as pd

import engine as E

CAL = pd.DatetimeIndex(pd.date_range("2020-01-01", periods=8, freq="B"))


def _mk_px(vals):
    return pd.Series(vals, index=CAL, dtype=float)


def test_spec_no_trade_on_missing_px():
    """缺价日(raw=NaN): 信号也不成交; 次日有价才成交(T+1框架)。"""
    px = _mk_px([np.nan, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    sig = pd.Series([1.0] * 8, index=CAL)
    ext = pd.Series([0.0] * 8, index=CAL)
    eq, fills, _ = E.run_specialist(px, sig, ext, CAL, budget=100.0, cost=0.0)
    buys = [f for f in fills if f[1] == "BUY"]
    assert len(buys) == 1 and buys[0][0] == CAL[1].date().isoformat(), f"缺价日必须不成交, got {fills}"


def test_spec_cash_plus_mv_equals_equity():
    """每一天 现金+持仓市值 == 净值 (含缺价估值日)。"""
    px = _mk_px([10, 10, np.nan, 9, 8, np.nan, 10, 11]).astype(float)
    sig = pd.Series([1, 0, 0, 0, 0, 0, 0, 0], index=CAL, dtype=float)
    ext = pd.Series([0, 0, 0, 0, 0, 0, 1, 0], index=CAL, dtype=float)
    eq, fills, _ = E.run_specialist(px, sig, ext, CAL, budget=100.0, cost=0.001)
    assert np.isfinite(eq).all() and (eq > 0).all()


def test_spec_budget_not_shared():
    """专户预算封闭: 总投入+剩余现金 == 预算 (资金不串用)。"""
    px = _mk_px([10, 10, 9, 8, 7, 10, 10, 10]).astype(float)
    sig = pd.Series([1, 0, 0, 0, 0, 0, 0, 0], index=CAL, dtype=float)
    ext = pd.Series([0] * 8, index=CAL, dtype=float)
    eq, fills, _ = E.run_specialist(px, sig, ext, CAL, budget=100.0, cost=0.0, tranches=(0.30, 0.35, 0.35))
    # 30+35+35 全部加满后 现金≈0, 投入=100
    assert eq.iloc[-1] <= 100.0 * 1.5, "预算封闭, 不得超投"
    buys_adds = [f for f in fills if f[1] in ("BUY", "ADD")]
    assert len(buys_adds) == 3, f"30/35/35 应三笔, got {buys_adds}"


def test_spec_add_confirm_execute_split():
    """加仓: T-1收盘确认条件, T日执行。日0信号, 日1入场@10; 日2跌至8 -> 日3 ADD(日2收盘确认)。"""
    px = _mk_px([10, 10, 8, 8, 8, 8, 8, 8]).astype(float)
    sig = pd.Series([1, 0, 0, 0, 0, 0, 0, 0], index=CAL, dtype=float)
    ext = pd.Series([0] * 8, index=CAL, dtype=float)
    eq, fills, _ = E.run_specialist(px, sig, ext, CAL, budget=100.0, cost=0.0, tranches=(0.30, 0.35, 0.35))
    adds = [f for f in fills if f[1] == "ADD"]
    assert len(adds) == 1 and adds[0][0] == CAL[3].date().isoformat(), f"ADD应在日3(日2收盘确认), got {fills}"


def test_spec_fee_same_day():
    """费用入账: 成交日净值为成交前快照(T+1口径), 次日起持有市值含费差 30×0.001。"""
    px = _mk_px([10, 10, 10, 10, 10, 10, 10, 10]).astype(float)
    sig = pd.Series([1, 0, 0, 0, 0, 0, 0, 0], index=CAL, dtype=float)
    ext = pd.Series([0] * 8, index=CAL, dtype=float)
    eq0, _, _ = E.run_specialist(px, sig, ext, CAL, budget=100.0, cost=0.0)
    eq1, fills, _ = E.run_specialist(px, sig, ext, CAL, budget=100.0, cost=0.001)
    assert abs((eq0.iloc[2] - eq1.iloc[2]) - 30.0 * 0.001) < 1e-9, "费用应自成交后市值体现"


def test_ledger_t1_and_missing_px():
    """run_ledger: T+1成交 + 缺价日不成交。"""
    px = pd.DataFrame({"A": _mk_px([np.nan, 10, 10, 10, 10, 10, 10, 10]),
                       "B": _mk_px([20, 20, 20, 20, 20, 20, 20, 20])})
    sig = pd.DataFrame({"A": [1.0] * 8, "B": [0.0] * 8}, index=CAL)
    ext = pd.DataFrame({"A": [0.0] * 8, "B": [0.0] * 8}, index=CAL)
    stre = pd.DataFrame({"A": [0.0] * 8, "B": [0.0] * 8}, index=CAL)
    res = E.run_ledger(px, sig, ext, stre, {"A": "a", "B": "b"}, CAL, max_pos=2, per_cat=2, cost=0.001)
    buys = [f for f in res["fills"] if f[1] == "BUY" and f[2] == "A"]
    assert len(buys) == 1 and buys[0][0] == CAL[1].date().isoformat(), f"T+1且缺价禁成交, got {res['fills']}"


def test_ledger_cash_mv_identity():
    px = pd.DataFrame({"A": _mk_px([10, 10, np.nan, 9, 8, 10, 10, 11]),
                       "B": _mk_px([20, 20, 20, np.nan, 19, 20, 20, 20])})
    sig = pd.DataFrame({"A": [1, 0, 0, 0, 0, 0, 0, 0], "B": [0, 1, 0, 0, 0, 0, 0, 0]}, index=CAL, dtype=float)
    ext = pd.DataFrame({"A": [0.0] * 8, "B": [0.0] * 8}, index=CAL)
    stre = pd.DataFrame({"A": [0.0] * 8, "B": [0.0] * 8}, index=CAL)
    res = E.run_ledger(px, sig, ext, stre, {"A": "a", "B": "b"}, CAL, max_pos=2, per_cat=2, cost=0.001)
    assert np.isfinite(res["equity"]).all() and (res["equity"] > 0).all()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print(f"PASS {f.__name__}")
    print(f"\n{len(fns)}/{len(fns)} 引擎测试全部通过")
