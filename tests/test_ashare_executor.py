# -*- coding: utf-8 -*-
"""Ashare-Stock 阶段1执行引擎负例: 时序无泄漏/涨跌停/停牌/整手/费用/账本。"""
import os
import sys

import pytest

sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import tempfile

from ashare_executor import Ledger, fees, limit_ratio, plan_order, plan_sell, round_lot, tradable


def test_limit_ratio():
    assert limit_ratio("600019") == 0.10 and limit_ratio("300750") == 0.20 and limit_ratio("688981") == 0.20
    assert limit_ratio("600019", is_st=True) == 0.05


def test_tradable_rejections():
    ok, why = tradable("600019", 11.00, 10.00)  # 涨停+10%
    assert not ok and why == "limit_up"
    ok, why = tradable("600019", 9.00, 10.00)  # 跌停
    assert not ok and why == "limit_down"
    ok, why = tradable("300750", 59.9, 50.0)  # 创业板20%内(59.9<60涨停价)→可成交
    assert ok and why == "ok"
    ok, why = tradable("600019", 10.5, 10.0, halted=True)
    assert not ok and why == "halted"
    ok, why = tradable("600019", 10.5, 10.0)  # 主板+5%正常
    assert ok


def test_lot_and_fees():
    assert round_lot(1234) == 1200 and round_lot(99) == 0
    buy_fee = fees("600019", 100000.0, False)
    sell_fee = fees("600019", 100000.0, True)
    assert buy_fee == pytest.approx(25.0 + 1.0, abs=0.01)  # 佣金25+过户1
    assert sell_fee == pytest.approx(25.0 + 50.0 + 1.0, abs=0.01)  # +印花税50
    assert fees("600019", 1000.0, False) == 5.01  # 最低佣金5元


def test_buy_fill_and_reject():
    f = plan_order("600019", "BUY", cash=10000.0, px_ref=10.0, px_t1=10.20, prev_close=10.0)
    assert f["status"] == "FILLED" and f["shares"] % 100 == 0 and f["shares"] > 0
    assert f["notional"] + f["fees"] <= 10000.0 + 1e-6
    assert f["signal_px"] == 10.0 and f["price"] == 10.20  # 信号T收盘, 成交T+1价
    r = plan_order("600019", "BUY", cash=10000.0, px_ref=10.0, px_t1=11.00, prev_close=10.0)
    assert r["status"] == "REJECTED" and r["reason"] == "limit_up"
    r = plan_order("600019", "BUY", cash=50.0, px_ref=10.0, px_t1=10.20, prev_close=10.0)
    assert r["status"] == "REJECTED" and r["reason"] == "insufficient_lot"


def test_sell_reject_bad_lot():
    r = plan_sell("600019", 150, px_ref=10.0, px_t1=10.2, prev_close=10.0)  # 非整手
    assert r["status"] == "REJECTED" and r["reason"] == "bad_lot"
    r = plan_sell("600019", 100, px_ref=10.0, px_t1=9.0, prev_close=10.0)  # 跌停卖不出(保守模拟)
    assert r["status"] == "REJECTED" and r["reason"] == "limit_down"


def test_ledger_no_leak_and_accounting():
    tmp = tempfile.mkdtemp()
    led = Ledger("M", nav0=1000.0, root=tmp)
    f = plan_order("600019", "BUY", cash=led.cash, px_ref=10.0, px_t1=10.20, prev_close=10.0)
    led.apply(f)
    assert led.cash == pytest.approx(1000.0 + f["cash_delta"], abs=0.01) and led.cash > 0
    assert led.pos["600019"] == f["shares"]
    s = plan_sell("600019", f["shares"], px_ref=10.2, px_t1=10.5, prev_close=10.2)
    led.apply(s)
    assert "600019" not in led.pos
    assert led.nav({"600019": 10.5}) == pytest.approx(led.cash, abs=0.01)
    with pytest.raises(AssertionError):
        led.apply({"code": "600019", "action": "SELL", "status": "REJECTED", "cash_delta": 0})  # 拒绝单不得入账
    with pytest.raises(AssertionError):
        led2 = Ledger("X", nav0=1000.0, root=tmp)
        led2.apply({"code": "600019", "action": "SELL", "status": "FILLED", "shares": 100, "cash_delta": 1000.0})  # 卖超持仓
