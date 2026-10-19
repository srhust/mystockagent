# -*- coding: utf-8 -*-
"""test_fetch_guard — 包E-2纪律单测(假时钟零真实等待)。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "..", "..", "cycle_system", "ashare_v2", "tools"))
import pytest                                        # noqa: E402
from fetch_guard import (CircuitOpen, FetchGuard, RateLimitExceeded,  # noqa: E402
                         classify_error, summarize_failures)


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def mk(cap=100, thr=5, reset=900.0, retries=3):
    c = FakeClock()
    g = FetchGuard(cap_calls=cap, max_retries=retries, breaker_threshold=thr,
                   breaker_reset_s=reset, sleeper=c.sleep, clock=c.now)
    return g, c


def test_success_path_stats():
    g, c = mk()
    assert g.call(lambda x: x * 2, 21) == 42
    s = g.stats()
    assert s["ok"] == 1 and s["fail"] == 0 and s["breaker"] == "CLOSED"


def test_network_error_backoff_then_success():
    g, c = mk(retries=3)
    seq = [ConnectionError("RemoteDisconnected"), ConnectionError("reset by peer"), "ok"]

    def fn():
        v = seq.pop(0)
        if isinstance(v, str):
            return v
        raise v
    assert g.call(fn) == "ok"
    s = g.stats()
    assert s["retry_sleeps"] == 2 and s["ok"] == 1
    assert len(c.slept) == 2 and c.slept[1] > c.slept[0]      # 指数增长
    assert s["error_distribution"]["network"] == 2


def test_cap_exceeded_explicit_reject():
    g, c = mk(cap=3)
    for _ in range(3):
        g.call(lambda: 1)
    with pytest.raises(RateLimitExceeded):
        g.call(lambda: 1)
    assert g.stats()["blocked"] == 1


def test_breaker_open_half_close_cycle():
    g, c = mk(thr=3, reset=900.0, retries=0)

    def bad():
        raise ConnectionError("proxy error")
    for _ in range(3):
        with pytest.raises(ConnectionError):
            g.call(bad)
    assert g.stats()["breaker"] == "OPEN"
    with pytest.raises(CircuitOpen):
        g.call(lambda: 1)
    c.t += 901.0                                            # 冷却到点
    assert g.call(lambda: "probe") == "probe"               # 半开探测成功
    assert g.stats()["breaker"] == "CLOSED"


def test_no_retry_for_data_facts():
    g, c = mk(retries=3)
    with pytest.raises(ValueError):
        g.call(lambda: (_ for _ in ()).throw(ValueError("数据缺少收盘列")))
    assert g.stats()["retry_sleeps"] == 0                    # format类不重试
    with pytest.raises(KeyError):
        g.call(lambda: (_ for _ in ()).throw(KeyError("无数据")))
    assert g.stats()["retry_sleeps"] == 0
    assert g.stats()["error_distribution"] == {"format": 1, "no_data": 1}


def test_classification_rules():
    assert classify_error(TimeoutError("read timeout")) == "network"
    assert classify_error(OSError("[WinError 10054] 远程主机强迫关闭")) == "network"
    assert classify_error(RuntimeError("股票600000不存在")) == "missing_code"
    assert classify_error(RuntimeError("unexpected parse 列 mismatch")) == "format"
    assert classify_error(RuntimeError("完全意外")) == "unknown"


def test_summarize_failures_distribution():
    missing = [{"code": "a", "err": "ProxyError: 代理拒绝"},
               {"code": "b", "err": "RemoteDisconnected: connection aborted"},
               {"code": "c", "err": "KeyError: 无数据"},
               {"code": "d", "err": "ValueError: 缺少列 open"}]
    d = summarize_failures(missing)
    assert d["network"]["n"] == 2 and d["no_data"]["n"] == 1 and d["format"]["n"] == 1
    assert d["network"]["types"] == {"ProxyError": 1, "RemoteDisconnected": 1}
