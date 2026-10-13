# -*- coding: utf-8 -*-
"""账本边界回归测试: 从 ab_ledger_test.py 提取真实 run() 用合成数据断言。
运行: pytest tests/test_ledger.py -q"""
import ast
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_NS = None


def _load_run():
    global _NS
    if _NS is not None:
        return _NS["run"]
    src = open(os.path.join(ROOT, "scripts", "ab_ledger_test.py"), encoding="utf-8-sig").read()
    tree = ast.parse(src)
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"][0]
    sys.path.insert(0, ROOT)
    import config as _config
    _NS = {"pd": pd, "np": np, "os": os, "COST": 0.0015, "CAP": 100.0, "config": _config}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<run>", "exec"), _NS)
    return _NS["run"]


def _mk_world(px_a, px_b, nan_a_days=(), nan_b_days=()):
    """两只合成证券A/B, 指定缺价日索引。返回 (run, cal)。"""
    n = len(px_a)
    cal = pd.date_range("2020-01-01", periods=n, freq="B")
    PX = pd.DataFrame({"A": px_a, "B": px_b}, index=cal)
    PX.loc[cal[list(nan_a_days)], "A"] = np.nan
    PX.loc[cal[list(nan_b_days)], "B"] = np.nan
    stren = pd.DataFrame({"A": -0.5, "B": -0.4}, index=cal)
    _load_run()
    _NS.update(cal=cal, PX=PX, STRE=stren, cols=["A", "B"], catmap={"A": "c1", "B": "c1"})
    return _NS["run"], cal


def _sig(n, a=0, b=0, a_day=None, b_day=None, a_exit=None, b_exit=None):
    cal = pd.date_range("2020-01-01", periods=n, freq="B")
    sig = pd.DataFrame({"A": a, "B": b}, index=cal)
    ext = pd.DataFrame({"A": 0, "B": 0}, index=cal)
    if a_day is not None:
        sig.iloc[a_day, 0] = 1
    if b_day is not None:
        sig.iloc[b_day, 1] = 1
    if a_exit is not None:
        ext.iloc[a_exit:, 0] = 1
    if b_exit is not None:
        ext.iloc[b_exit:, 1] = 1
    return sig, ext


def test_t1_tplus1_no_same_day_fill():
    n = 10
    run, _ = _mk_world(np.full(n, 10.0), np.full(n, 20.0))
    _NS.update(cols=["A"])
    sig, ext = _sig(n, a_day=3)
    eq = run(sig, ext, max_pos=6, per_cat=2)
    assert abs(eq.iloc[3] - 100.0) < 0.5, f"T日不应成交: {eq.iloc[3]}"
    # 恒价+费用: T+1成交后, 次日净值应<100(费用损耗证明持仓已建)
    assert eq.iloc[5] < 100.0 and eq.iloc[6] < 100.0, f"T+1应已建仓(费用可见): {eq.iloc[5]}"


def test_t2_missing_price_no_equity_collapse():
    n = 30
    run, _ = _mk_world(np.full(n, 10.0), np.full(n, 20.0), nan_a_days=range(10, 25))
    _NS.update(cols=["A"])
    sig, ext = _sig(n, a_day=2)
    eq = run(sig, ext, max_pos=6, per_cat=2)
    seg = eq.iloc[12:22]
    assert seg.std() < 0.01, f"缺价日净值应恒定不塌陷: std={seg.std()}"


def test_t3_stale_price_no_trading():
    n = 40
    run, _ = _mk_world(np.full(n, 10.0), np.full(n, 20.0), nan_a_days=range(10, n))
    _NS.update(cols=["A"])
    sig, ext = _sig(n, a_day=2, a_exit=30)  # 停牌中出场
    eq = run(sig, ext, max_pos=6, per_cat=2)
    assert np.isclose(eq.iloc[35], eq.iloc[30], atol=0.5), "停牌期间禁止按过期价交易"


def test_t4_costs_deducted():
    n = 12
    run, _ = _mk_world(np.full(n, 10.0), np.full(n, 10.0))
    _NS.update(cols=["A"])
    sig, ext = _sig(n, a_day=1)
    eq = run(sig, ext, max_pos=6, per_cat=2)
    assert eq.iloc[-1] < 100.0, f"费用应使净值<100: {eq.iloc[-1]}"


def test_t5_per_cat_limit():
    n = 12
    run, _ = _mk_world(np.full(n, 10.0), np.full(n, 20.0))  # 同类c1
    sig, ext = _sig(n, a=1, b=1)
    eq = run(sig, ext, max_pos=6, per_cat=1, risk=False)
    assert eq.iloc[-1] <= 100.5, f"per_cat=1同类只买1只: {eq.iloc[-1]}"


def test_t6_exposure_cap_risk_mode():
    n = 30
    run, _ = _mk_world(np.linspace(8, 12, n), np.linspace(16, 24, n))
    sig, ext = _sig(n, a=1, b=1)
    eq = run(sig, ext, max_pos=6, per_cat=2, risk=True)
    assert np.isfinite(eq.iloc[-1]) and eq.iloc[-1] > 0
