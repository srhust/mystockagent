# -*- coding: utf-8 -*-
"""Ashare-Stock 阶段1 R2 负例: 计划/成交分离·分期交易制度·原子账本·T+1可卖·费用守恒。"""
import hashlib
import json
import sys
import tempfile

import pandas as pd
import pytest

sys.path.insert(0, r"E:\mystockagent\cycle_system")
from ashare_executor import Ledger, create_plan, execute_plan, fees, limit_ratio, lot_step, round_lot


def test_limit_ratio_by_date():
    # 主板ST: 旧规5% → 2026-07-06新规10%
    assert limit_ratio("600019", "2026-01-05", is_st=True) == 0.05
    assert limit_ratio("600019", "2026-07-06", is_st=True) == 0.10
    # A1: 创业板注册制(2020-08-24)前普通10%/ST5%; 2025年ST=20%
    assert limit_ratio("300750", "2020-08-21") == 0.10
    assert limit_ratio("300750", "2020-08-21", is_st=True) == 0.05
    assert limit_ratio("300750", "2020-08-24") == 0.20
    assert limit_ratio("300750", "2025-06-02", is_st=True) == 0.20

    # 科创板恒20%(含ST)
    assert limit_ratio("688981", "2020-01-01", is_st=True) == 0.20


def test_lot_rules():
    assert lot_step("688981") == (200, 1) and lot_step("600019") == (100, 100)
    assert round_lot("688981", 250) == 250 and round_lot("688981", 201) == 201
    assert round_lot("688981", 150) == 0
    assert round_lot("600019", 1234) == 1200 and round_lot("600019", 99) == 0


def test_plan_execution_separation():
    """P0-4: create_plan签名零T+1信息; execute_plan验哈希+消费。"""
    plan = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=20000.0)
    assert "px_t1" not in plan and "price" not in plan
    assert plan["trade_date"] == "2026-10-15" and len(plan["plan_hash"]) == 12
    body = json.dumps({k: v for k, v in plan.items() if k != "plan_hash"}, sort_keys=True, ensure_ascii=False)
    assert hashlib.sha1(body.encode()).hexdigest()[:12] == plan["plan_hash"]
    f = execute_plan(plan, "2026-10-16", px_t1=10.20, prev_close=10.0)
    assert f["status"] == "FILLED" and f["price"] == 10.20 and f["signal_px"] == 10.0
    assert f["shares"] % 100 == 0 and f["notional"] + f["fees"] <= 20000.0
    with pytest.raises(AssertionError, match="下一交易日"):
        execute_plan(plan, "2026-10-15", px_t1=10.20, prev_close=10.0)  # A2: 同日执行拒绝
    with pytest.raises(AssertionError, match="下一交易日"):
        execute_plan(plan, "2026-10-19", px_t1=10.20, prev_close=10.0)  # A2: 跳日拒绝
    bad = dict(plan)
    bad["cash_budget"] = 999999.0
    with pytest.raises(AssertionError, match="计划哈希"):
        execute_plan(bad, "2026-10-16", px_t1=10.20, prev_close=10.0)


def test_p0_1_insufficient_cash_after_fees():
    """现金1024买10.20: 100股+费用1025.01>1024 → 减至0股 → 必须拒绝。"""
    plan = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=1024.0)
    r = execute_plan(plan, "2026-10-16", px_t1=10.20, prev_close=10.0)
    assert r["status"] == "REJECTED" and r["reason"] == "insufficient_cash_after_fees"
    assert "cash_delta" not in r and "fees" not in r
    plan2 = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=1026.0)
    f2 = execute_plan(plan2, "2026-10-16", px_t1=10.20, prev_close=10.0)
    assert f2["status"] == "FILLED" and f2["shares"] == 100


def test_limit_and_halt_rejections():
    plan = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=20000.0)
    r = execute_plan(plan, "2026-10-16", px_t1=11.00, prev_close=10.0)
    assert r["status"] == "REJECTED" and r["reason"] == "limit_up"
    plan_st = create_plan("600019", "BUY", "2026-01-05", px_ref=10.0, cash_budget=20000.0, created_at="2026-01-05T16:00:00+08:00")
    r = execute_plan(plan_st, "2026-01-06", px_t1=10.60, prev_close=10.0, is_st=True)
    assert r["status"] == "REJECTED" and r["reason"] == "limit_up"  # 旧规涨停价10.50, 10.60必拒(>hi)
    r = execute_plan(plan_st, "2026-01-06", px_t1=10.50, prev_close=10.0, is_st=True)
    assert r["status"] == "REJECTED" and r["reason"] == "limit_up"  # 恰触板拒绝
    r = execute_plan(plan_st, "2026-01-06", px_t1=10.49, prev_close=10.0, is_st=True)
    assert r["status"] == "FILLED" and r["price"] == 10.49
    plan_h = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=20000.0)
    r = execute_plan(plan_h, "2026-10-16", px_t1=10.50, prev_close=10.0, halted=True)
    assert r["status"] == "REJECTED" and r["reason"] == "halted"


def test_ledger_atomic_and_t1_sellable():
    """P0-2/P0-5: 异常零副作用(内存+文件字节级不变); 同日买入不可卖, 跨日可卖。"""
    tmp = tempfile.mkdtemp()
    led = Ledger("M", nav0=20000.0, root=tmp)
    led.advance_day("2026-10-15")
    plan = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=20000.0)
    f = execute_plan(plan, "2026-10-16", px_t1=10.20, prev_close=10.0)
    led.apply(f)
    assert led.pos["600019"] == {"total": f["shares"], "sellable": 0}
    snap_file = open(led.log_p, "rb").read()
    snap = (led.cash, json.dumps(led.pos, sort_keys=True))
    with pytest.raises(AssertionError, match="卖出超过"):
        led.apply({"code": "600019", "action": "SELL", "status": "FILLED", "plan_hash": "m1", "shares": f["shares"], "cash_delta": 19934.83, "price": 10.5, "notional": 19950.0, "fees": 15.17})
    assert (led.cash, json.dumps(led.pos, sort_keys=True)) == snap
    assert open(led.log_p, "rb").read() == snap_file
    with pytest.raises(AssertionError, match="严格前进"):
        led.advance_day("2026-10-15")  # A4: 同日重复切换拒绝(账本日仍为10-15)
    led.advance_day("2026-10-16")
    assert led.pos["600019"]["sellable"] == f["shares"]
    # A3: 负股数/金额不守恒/重复成交拒绝
    with pytest.raises(AssertionError, match="正整数"):
        led.apply({"code": "600019", "action": "BUY", "status": "FILLED", "plan_hash": "fakeA3a", "shares": -100, "cash_delta": 1000.0, "price": 10.0, "notional": -1000.0, "fees": 5.0})
    with pytest.raises(AssertionError, match="守恒"):
        led.apply({"code": "600019", "action": "BUY", "status": "FILLED", "plan_hash": "fakeA3b", "shares": 100, "cash_delta": -1000.0, "price": 10.0, "notional": 5000.0, "fees": 5.0})
    okfill = {"code": "600019", "action": "BUY", "status": "FILLED", "plan_hash": "dup1", "shares": 100, "cash_delta": -505.01, "price": 5.0, "notional": 500.0, "fees": 5.01}
    led.apply(okfill)
    with pytest.raises(AssertionError, match="重复成交"):
        led.apply(okfill)  # A3: 同plan_hash重复入账拒绝
    led.advance_day("2026-10-18")
    s = create_plan("600019", "SELL", "2026-10-16", px_ref=10.2, shares=f["shares"], created_at="2026-10-16T16:00:00+08:00")
    sf = execute_plan(s, "2026-10-19", px_t1=10.50, prev_close=10.2)
    led.apply(sf)
    assert led.pos["600019"] == {"total": 100, "sellable": 100} and led.cash == pytest.approx(20000.0 + f["cash_delta"] + sf["cash_delta"] + okfill["cash_delta"], abs=0.01)  # 余100股=okfill买入
    snap2_file = open(led.log_p, "rb").read()
    snap2 = (led.cash, json.dumps(led.pos, sort_keys=True))
    with pytest.raises(AssertionError, match="卖出超过"):
        led.apply({"code": "600019", "action": "SELL", "status": "FILLED", "plan_hash": "over1", "shares": 200, "cash_delta": 2093.93, "price": 10.5, "notional": 2100.0, "fees": 6.07})
    assert (led.cash, json.dumps(led.pos, sort_keys=True)) == snap2, "A6: 超卖异常后状态字节级不变"
    assert open(led.log_p, "rb").read() == snap2_file, "A6: 超卖异常后日志字节级不变"
    with pytest.raises(AssertionError, match="仅FILLED"):
        led.apply({"code": "600019", "action": "BUY", "status": "REJECTED", "reason": "limit_up", "cash_delta": 0})
    # A5: 科创板余额<200一次性卖出(账本验证total==shares)
    led5 = Ledger("S5", nav0=200000.0, root=tmp)
    led5.advance_day("2026-10-15")
    led5.apply({"code": "688981", "action": "BUY", "status": "FILLED", "plan_hash": "star1", "shares": 250, "cash_delta": -25005.25, "price": 100.0, "notional": 25000.0, "fees": 5.25})
    led5.advance_day("2026-10-16")
    s5 = create_plan("688981", "SELL", "2026-10-16", px_ref=100.0, shares=200, created_at="2026-10-16T16:00:00+08:00")
    led5.apply(execute_plan(s5, "2026-10-19", px_t1=101.0, prev_close=100.0))
    s5b = create_plan("688981", "SELL", "2026-10-16", px_ref=101.0, shares=50, created_at="2026-10-16T16:00:00+08:00")
    f5b = execute_plan(s5b, "2026-10-19", px_t1=100.5, prev_close=101.0, allow_residual_sell=True)
    assert f5b["status"] == "FILLED", "A5: 科创板余额50股一次性卖出应允许"
    led5.apply(f5b)
    assert "688981" not in led5.pos
    r5 = execute_plan(create_plan("688981", "SELL", "2026-10-16", px_ref=100.0, shares=50, created_at="2026-10-16T16:00:00+08:00"), "2026-10-19", px_t1=99.0, prev_close=100.0)
    assert r5["status"] == "REJECTED" and r5["reason"] == "bad_lot"  # 不开余额开关时仍拒绝


def test_nav_missing_price_and_fees():
    led = Ledger("X", nav0=20000.0, root=tempfile.mkdtemp())
    led.advance_day("2026-10-15")
    plan = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=20000.0)
    f = execute_plan(plan, "2026-10-16", px_t1=10.20, prev_close=10.0)
    led.apply(f)
    with pytest.raises(KeyError, match="缺价"):
        led.nav({})
    assert led.nav({"600019": 10.20}) == pytest.approx(led.cash + f["shares"] * 10.20, abs=0.01)
    assert fees("600019", 100000.0, True) == pytest.approx(25.0 + 50.0 + 1.0, abs=0.01)
    assert fees("600019", 1000.0, False) == 5.01
