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
    assert not cal.is_trading_day("2026-10-10")  # 周六
    assert str(cal._next_bdate("2026-09-30").date()) == "2026-10-08"  # 跨国庆顺延
    # D1: 2026春节休市(2/15-23)正确拒绝 — 经真实推导日历
    assert not cal.is_trading_day("2026-02-17") and not cal.is_trading_day("2026-02-20")
    assert cal.is_trading_day("2026-02-24")


def test_d1_d2_d3_failclosed():
    """D1正常包导入+fail-closed; D2三态+来源纯净; D3缓存身份与失效重建。"""
    # D1: 不插sys.path, 正常包导入路径下春节拒绝(排除测试专用path依赖)
    import importlib, sys as _sys
    mod = importlib.import_module("cycle_system.ashare_executor")
    assert mod.is_trading_day("2026-09-28") is True
    with pytest.raises(RuntimeError, match="fail-closed|未确认|不可用|日历"):
        mod.is_trading_day("2027-06-01")  # 历史+官方日程皆不可及→拒绝而非猜测
    assert mod.is_trading_day("2026-10-20") is True  # E2: 官方日程确认前向交易日
    # D2: 三态 + etfdiv不参与来源 + 周末污染门禁
    assert cal.calendar_status("2026-09-29") == cal.CONFIRMED_OPEN
    assert cal.calendar_status("2026-10-01") == cal.CONFIRMED_CLOSED
    assert cal.calendar_status("2026-10-20") == cal.CONFIRMED_OPEN  # E2: 官方日程前向确认(原UNKNOWN已修复)
    assert all("etfdiv" not in os.path.basename(s) for s in cal._sources())
    # D3: 缓存身份(meta sha256)+损坏自动重建+force失效内存
    with open(cal.META_P, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["n_sources"] >= 20 and meta["range"][1] >= "2026-10-13"
    cal._CACHE = {"2000-01-01"}  # 破坏内存缓存
    cal.build_cache(force=True)  # force失效内存并重建
    assert len(cal._load()) > 3000
    with open(cal.CACHE_P, "w", encoding="utf-8") as f:
        f.write("garbage")  # 损坏磁盘缓存
    assert cal.calendar_status("2026-09-29") == cal.CONFIRMED_OPEN  # 自动重建成功
    cal.build_cache(force=True)  # 恢复规范缓存


def test_d4_stock_pipeline():
    """D4: 个股逐日OHLCV真实落盘+可用性审计(数据源akshare, 审计如实在仓库)。"""
    import ashare_stock_pipeline as sp
    a = sp.run(max_stocks=3)  # 断点续传: 已落盘的3只直接CACHED
    assert a["_summary"]["ok"] >= 3
    rep = sp.availability_report()
    assert rep["total_configured"] >= 3 and rep["tradeable_pool"]
    with open(os.path.join(sp.DATA_DIR, "601088.csv"), encoding="utf-8") as f:
        head = f.readline().strip()
    assert head.startswith("date,open,high,low,close")  # 逐日OHLCV结构


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


def test_e3_tradeable_pool():
    """E3: 历史可投资池 — 上市/停牌近似/交易日fail-closed/诚实边界。"""
    import ashare_tradeable_pool as tp
    r = tp.pool_at("2026-09-28")
    assert 200 <= r["stats"]["tradeable"] <= 249
    assert r["caveats"]["st_history"] == "UNAVAILABLE"  # ST历史不猜测
    r2 = tp.pool_at("2022-06-15")
    assert r2["stats"]["not_yet_listed"] > 0  # 2022年仍有后来上市股
    assert r2["stats"]["tradeable"] < r["stats"]["tradeable"]
    with pytest.raises(RuntimeError, match="非确认交易日"):
        tp.pool_at("2026-10-03")  # 国庆fail-closed

