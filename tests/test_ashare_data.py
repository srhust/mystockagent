# -*- coding: utf-8 -*-
"""R7: 交易日历推导 + 数据管道审计 + executor接入。"""
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import ashare_calendar as cal
import ashare_data_pipeline as dp
import ashare_executor as ex


def test_calendar_derivation_and_cross_check():
    c = cal.build_cache(force=True)
    assert len(c) > 3000  # 2012~2026 市场成交日并集
    ck = cal.validate_cross_check()
    assert ck["violations"] == []  # 官方休市日不得出现在推导日历
    assert not cal.is_trading_day("2026-10-01") and not cal.is_trading_day("2026-10-03")  # 国庆
    assert cal.is_trading_day("2026-10-08") and cal.is_trading_day("2026-10-13")
    assert not cal.is_trading_day("2026-10-17")  # 周六
    assert str(cal._next_bdate("2026-09-30").date()) == "2026-10-08"  # 跨国庆顺延


def test_executor_uses_derived_calendar():
    assert ex.is_trading_day("2026-10-08") and not ex.is_trading_day("2026-10-01")
    assert str(ex._next_bdate("2026-09-30").date()) == "2026-10-08"  # executor经calendar模块顺延
    # 节假日advance_day拒绝
    led = ex.Ledger("CAL", nav0=20000.0, root=os.path.join(os.environ.get("TEMP", r"E:\AI_Cache\temp"), "caltest"), test_mode=True)
    with pytest.raises(AssertionError, match="非A股交易日"):
        led.advance_day("2026-10-01")


def test_pipeline_audit_structure():
    o = dp.audit()
    assert o["calendar"]["days"] > 3000
    assert o["sectors_total"] == 30 and o["stocks_total"] >= 250
    assert len(o["etf_proxies"]) == 26 and len(o["no_proxy_sectors"]) == 4
    withgaps = [r for r in o["etf_proxies"] if r["gaps_within"] > 0]
    assert all(r["ok"] == (r["gaps_within"] == 0 and r["lag_trading_days"] <= 5) for r in o["etf_proxies"])  # 状态分级如实
    print("GAPS:", [(r["sector"], r["gaps_within"]) for r in withgaps])  # LOF稀疏成交/半日市缺口由审计报告暴露
    assert "个股CSV" in o["individual_stock_data"] or "ABSENT" in o["individual_stock_data"]  # 如实披露个股级缺位
    for p in ("data_audit.md", "data_audit.json"):
        assert os.path.exists(os.path.join(dp.OUT_DIR, p))
