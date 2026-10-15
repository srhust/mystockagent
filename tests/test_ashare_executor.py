# -*- coding: utf-8 -*-
"""Ashare-Stock 阶段1 R2 负例: 计划/成交分离·分期交易制度·原子账本·T+1可卖·费用守恒。"""
import hashlib
import json
import os
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
    led = Ledger("M", nav0=20000.0, root=tmp, test_mode=True)
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

def test_b1_b2_b3_identity_and_lots():
    """B1: 计划—成交字段级绑定; B2: unplanned仅test_mode+scoring强制False; B3: 主板卖出整手。"""
    tmp = tempfile.mkdtemp()
    led = Ledger("PROD", nav0=20000.0, root=tmp)  # 正式账本(无test_mode)
    led.advance_day("2026-10-16")
    bp = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=20000.0)
    led.register_plan(bp)
    with pytest.raises(AssertionError, match="test_mode"):
        led.apply({"code": "600019", "action": "BUY", "status": "FILLED", "plan_hash": "nofake", "shares": 100, "cash_delta": -1025.01, "price": 10.2, "notional": 1020.0, "fees": 5.01, "execution_date": "2026-10-16", "signal_px": 10.0}, allow_unplanned=True)  # B2: 正式账本禁unplanned
    # B1: 登记BUY计划后提交SELL成交→拒绝; 方向/股票/预算不符→拒绝
    # B1: 同plan_hash换方向在PROD账本被拒(账本测试转入B1负例段)
    led2 = Ledger("P2", nav0=20000.0, root=tmp)
    led2.advance_day("2026-10-16")
    led2.register_plan(bp)
    bf = execute_plan(bp, "2026-10-16", px_t1=10.2, prev_close=10.0)
    led2.apply(bf)  # 正式通道: 计划—成交绑定通过
    assert led2.pos["600019"]["total"] == bf["shares"]
    # B1负例: 登记BUY计划(未执行)→提交同hash的SELL成交(方向不符)→拒绝且零副作用; 股票不符同理
    snap = (led2.cash, json.dumps(led2.pos, sort_keys=True))
    bp2 = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=50000.0)
    led2.register_plan(bp2)  # 登记但未执行
    with pytest.raises(AssertionError, match="股票/方向"):
        led2.apply({"code": "600019", "action": "SELL", "status": "FILLED", "plan_hash": bp2["plan_hash"], "shares": 100, "cash_delta": 1000.0, "price": 10.0, "notional": 1000.0, "fees": 5.0, "execution_date": "2026-10-16", "signal_px": 10.0})
    with pytest.raises(AssertionError, match="股票/方向"):
        led2.apply({"code": "000630", "action": "BUY", "status": "FILLED", "plan_hash": bp2["plan_hash"], "shares": 100, "cash_delta": -1005.01, "price": 10.0, "notional": 1000.0, "fees": 5.01, "execution_date": "2026-10-16", "signal_px": 10.0})
    assert (led2.cash, json.dumps(led2.pos, sort_keys=True)) == snap
    # B3: 主板卖出150股 execute_plan拒绝; 不足一手余额经开关+账本total验证
    r = execute_plan(create_plan("600019", "SELL", "2026-10-15", px_ref=10.0, shares=150, created_at="2026-10-15T16:00:00+08:00"), "2026-10-16", px_t1=10.2, prev_close=10.0)
    assert r["status"] == "REJECTED" and r["reason"] == "bad_lot"
    # B2: 测试账本unplanned成交scoring强制False且审计标记
    ledT = Ledger("TEST1", nav0=20000.0, root=tmp, test_mode=True)
    ledT.advance_day("2026-10-16")
    ledT.apply({"code": "600019", "action": "BUY", "status": "FILLED", "plan_hash": "t1", "shares": 100, "cash_delta": -1025.01, "price": 10.2, "notional": 1020.0, "fees": 5.01, "execution_date": "2026-10-16", "signal_px": 10.0, "scoring": True}, allow_unplanned=True)
    last = json.loads(open(ledT.log_p, encoding="utf-8").readlines()[-1])
    assert last["unplanned"] is True and last["scoring"] is False  # 强制不计分


def test_c1_c2_c3_end_to_end():
    """C1: 正式SELL端到端; C2: 伪造零费用拒绝+四项不变性; C3: 主板余额卖出闭环。"""
    tmp = tempfile.mkdtemp()
    # C1: register → execute_plan(SELL) → apply 成功(捕获shares赋值顺序错误)
    led = Ledger("C1", nav0=20000.0, root=tmp, test_mode=True)
    led.advance_day("2026-10-16")
    led.apply({"code": "600019", "action": "BUY", "status": "FILLED", "plan_hash": "seed1", "shares": 100, "cash_delta": -1025.01, "price": 10.2, "notional": 1020.0, "fees": 5.01, "execution_date": "2026-10-16", "signal_px": 10.0}, allow_unplanned=True)
    led.advance_day("2026-10-19")
    sp = create_plan("600019", "SELL", "2026-10-16", px_ref=10.2, shares=100, created_at="2026-10-16T16:00:00+08:00")
    led.register_plan(sp)
    sf = execute_plan(sp, "2026-10-19", px_t1=10.5, prev_close=10.2)
    assert sf["status"] == "FILLED" and sf["shares"] == 100
    led.apply(sf)  # R6前: UnboundLocalError在此触发
    assert "600019" not in led.pos
    nav1 = led.nav({"600019": 10.5})
    assert abs(nav1 - (20000.0 - 1025.01 + sf["cash_delta"])) < 0.01  # NAV守恒
    # C2: 已登记BUY计划+伪造零费用→拒绝; cash/pos/去重/日志四项不变
    led2 = Ledger("C2", nav0=20000.0, root=tmp, test_mode=True)
    led2.advance_day("2026-10-16")
    bp = create_plan("600019", "BUY", "2026-10-15", px_ref=10.0, cash_budget=20000.0)
    led2.register_plan(bp)
    fake = {"code": "600019", "action": "BUY", "status": "FILLED", "plan_hash": bp["plan_hash"], "execution_date": "2026-10-16", "shares": 100, "price": 10.0, "notional": 1000.0, "fees": 0.0, "cash_delta": -1000.0, "signal_px": 10.0}
    snap_cash, snap_pos = led2.cash, json.dumps(led2.pos, sort_keys=True)
    assert not os.path.exists(led2.log_p)  # 首笔成交前无日志文件
    open(led2.log_p, "wb").close()  # 预建空日志以做字节级对比
    snap_log = open(led2.log_p, "rb").read()
    snap_exec = set(getattr(led2, "_executed", set()))
    with pytest.raises(AssertionError, match="费用与账本独立复算不符"):
        led2.apply(fake)  # fees=0 vs 复算5.01
    assert led2.cash == snap_cash and json.dumps(led2.pos, sort_keys=True) == snap_pos
    assert set(getattr(led2, "_executed", set())) == snap_exec and open(led2.log_p, "rb").read() == snap_log
    # C3: 主板余额卖出闭环 — 100整手+50零股
    led3 = Ledger("C3", nav0=20000.0, root=tmp, test_mode=True)
    led3.advance_day("2026-10-16")
    led3.apply({"code": "600019", "action": "BUY", "status": "FILLED", "plan_hash": "seed3", "shares": 150, "cash_delta": -1535.02, "price": 10.2, "notional": 1530.0, "fees": 5.02, "execution_date": "2026-10-16", "signal_px": 10.0}, allow_unplanned=True)
    led3.advance_day("2026-10-19")
    s100 = create_plan("600019", "SELL", "2026-10-16", px_ref=10.2, shares=100, created_at="2026-10-16T16:00:00+08:00")
    led3.register_plan(s100)
    f100 = execute_plan(s100, "2026-10-19", px_t1=10.5, prev_close=10.2)
    led3.apply(f100)  # 整手100正常卖出
    assert led3.pos["600019"] == {"total": 50, "sellable": 50}
    s50 = create_plan("600019", "SELL", "2026-10-16", px_ref=10.5, shares=50, created_at="2026-10-16T16:00:00+08:00")
    led3.register_plan(s50)
    f50 = execute_plan(s50, "2026-10-19", px_t1=10.6, prev_close=10.5, allow_residual_sell=True)
    assert f50["status"] == "FILLED"  # R6前: 主板50股被整手检查提前拦截bad_lot
    led3.apply(f50)  # 账本: 50<100 → total==50 ✓ 全部余额
    assert "600019" not in led3.pos
    # C3负例: 未全部卖出→账本拒绝
    led4 = Ledger("C4", nav0=20000.0, root=tmp, test_mode=True)
    led4.advance_day("2026-10-16")
    led4.apply({"code": "600019", "action": "BUY", "status": "FILLED", "plan_hash": "seed4", "shares": 250, "cash_delta": -2555.03, "price": 10.2, "notional": 2550.0, "fees": 5.03, "execution_date": "2026-10-16", "signal_px": 10.0}, allow_unplanned=True)
    led4.advance_day("2026-10-19")
    led4.advance_day("2026-10-20")
    with pytest.raises(AssertionError, match="全部剩余持仓"):
        led4.apply({"code": "600019", "action": "SELL", "status": "FILLED", "plan_hash": "r4a", "shares": 30, "cash_delta": 309.84, "price": 10.5, "notional": 315.0, "fees": 5.16, "execution_date": "2026-10-20", "signal_px": 10.5}, allow_unplanned=True)  # 30<100但非全部余额


def test_nav_missing_price_and_fees():
    led = Ledger("X", nav0=20000.0, root=tempfile.mkdtemp(), test_mode=True)
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
