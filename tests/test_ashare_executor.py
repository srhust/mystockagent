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
    """R4: 可信通道+execution_date绑定+同日不可卖+节假日日历+原子性。"""
    tmp = tempfile.mkdtemp()
    led = Ledger("M", nav0=20000.0, root=tmp)
    led.advance_day("2026-10-15")
    plan = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=20000.0)
    led.register_plan(plan)  # N2: 可信通道
    f = execute_plan(plan, "2026-10-16", px_t1=10.20, prev_close=10.0)
    assert f["execution_date"] == "2026-10-16"
    with pytest.raises(AssertionError, match="与账本交易日不一致"):
        led.apply(f)  # N3: 账本日仍为10-15, 10-16成交不得入账
    led.advance_day("2026-10-16")
    led.apply(f)
    assert led.pos["600019"] == {"total": f["shares"], "sellable": 0}
    snap_file = open(led.log_p, "rb").read()
    snap = (led.cash, json.dumps(led.pos, sort_keys=True))
    with pytest.raises(AssertionError, match="卖出超过"):
        led.apply({**f, "action": "SELL", "plan_hash": "m1", "cash_delta": 19934.83, "notional": 19950.0, "fees": 15.17, "shares": f["shares"], "price": 10.5}, allow_unplanned=True)
    assert (led.cash, json.dumps(led.pos, sort_keys=True)) == snap and open(led.log_p, "rb").read() == snap_file
    # N2: 伪造负费用与无来源成交必须拒绝
    fake = {**f, "action": "SELL", "plan_hash": "fake123", "cash_delta": 900.0, "notional": 1000.0, "fees": -100.0, "shares": 100, "price": 10.0}
    with pytest.raises(AssertionError, match="费用必须非负"):
        led.apply(fake, allow_unplanned=True)
    fake2 = {**fake, "fees": 0.0, "cash_delta": 1000.0}
    with pytest.raises(AssertionError, match="冻结计划"):
        led.apply(fake2)  # 无登记计划→拒绝(不开allow_unplanned)
    # A4: 同日重复切换拒绝
    with pytest.raises(AssertionError, match="严格前进"):
        led.advance_day("2026-10-16")
    # N4: 节假日日历 — 国庆前计划顺延到10-08; 10-01不是交易日
    from ashare_executor import is_trading_day, _next_bdate
    assert not is_trading_day("2026-10-01") and is_trading_day("2026-10-08")
    assert str(_next_bdate("2026-09-30").date()) == "2026-10-08"
    hp = create_plan("600019", "BUY", "2026-09-30", px_ref=10.0, cash_budget=20000.0, created_at="2026-09-30T16:00:00+08:00")
    led.register_plan(hp)
    hf = execute_plan(hp, "2026-10-08", px_t1=10.20, prev_close=10.0)  # 跨国庆顺延成交
    assert hf["status"] == "FILLED" and hf["execution_date"] == "2026-10-08"
    with pytest.raises(AssertionError, match="下一交易日"):
        execute_plan(hp, "2026-10-01", px_t1=10.20, prev_close=10.0)  # 节假日执行拒绝
    # N1: 执行日开盘后(15:30)生成的计划必须拒绝
    late = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=20000.0, created_at="2026-10-16T15:30:00+08:00")
    led.register_plan(late)
    with pytest.raises(AssertionError, match="窗口内"):
        execute_plan(late, "2026-10-16", px_t1=10.20, prev_close=10.0)
    # 重复成交拒绝(同plan_hash)
    led.register_plan(plan)
    with pytest.raises(AssertionError, match="重复成交"):
        led.apply(f)
    # 跨日释放: advance到10-19(下一交易日)后原持仓可卖
    led.advance_day("2026-10-19")
    assert led.pos["600019"]["sellable"] == f["shares"]
    # A3: 负股数/守恒/重复身份(测试构造通道, 审计标记unplanned)
    with pytest.raises(AssertionError, match="正整数"):
        led.apply({"code": "600019", "action": "BUY", "status": "FILLED", "plan_hash": "fakeA3a", "shares": -100, "cash_delta": 1000.0, "price": 10.0, "notional": -1000.0, "fees": 5.0, "execution_date": "2026-10-19"}, allow_unplanned=True)
    with pytest.raises(AssertionError, match="守恒"):
        led.apply({"code": "600019", "action": "BUY", "status": "FILLED", "plan_hash": "fakeA3b", "shares": 100, "cash_delta": -1000.0, "price": 10.0, "notional": 5000.0, "fees": 5.0, "execution_date": "2026-10-19"}, allow_unplanned=True)
    with pytest.raises(AssertionError, match="仅FILLED"):
        led.apply({"code": "600019", "action": "BUY", "status": "REJECTED", "reason": "limit_up", "cash_delta": 0}, allow_unplanned=True)

def test_nav_missing_price_and_fees():
    led = Ledger("X", nav0=20000.0, root=tempfile.mkdtemp())
    led.advance_day("2026-10-16")
    plan = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=20000.0)
    led.register_plan(plan)
    f = execute_plan(plan, "2026-10-16", px_t1=10.20, prev_close=10.0)
    led.apply(f)
    with pytest.raises(KeyError, match="缺价"):
        led.nav({})
    assert led.nav({"600019": 10.20}) == pytest.approx(led.cash + f["shares"] * 10.20, abs=0.01)
    assert fees("600019", 100000.0, True) == pytest.approx(25.0 + 50.0 + 1.0, abs=0.01)
    assert fees("600019", 1000.0, False) == 5.01
