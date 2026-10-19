# -*- coding: utf-8 -*-
"""fetch_guard — 包E-2: 数据通道请求纪律(裁定E-2: 错误分布+请求上限+指数退避+熔断)。

设计动机(§5事故): 一次多年窗重建530+次调用全部以同类错误崩溃, 且循环无上限无退避,
疑似自我触发限流(SUSPECTED_RATE_LIMIT_OR_CONNECTION_REJECTION)。本模块把通道纪律
固化为一等对象, 供forward_ingest扩展层/2000扩展POC/包D事件抓取统一复用。

纪律:
  1. 请求上限: 滚动窗口内最多 cap_calls 次 且 累计不超过 cap_bytes(粗算按次);
     超限 → RateLimitExceeded(显式, 不静默排队)
  2. 指数退避: 可重试错误按 base*(mult**attempt) 秒退避(带抖动), 最多 max_retries;
     等待经sleeper注入(测试假时钟)
  3. 熔断: 连续 breaker_threshold 次**网络类**失败 → OPEN, 冷却 breaker_reset 秒;
     OPEN期间调用直接拒绝; 半开探测一次成功即CLOSED
  4. 错误分布: 每次失败归类 network / no_data / missing_code / format / unknown,
     stats()输出计数与首见样例(截断), 崩溃不再是单一RuntimeError黑箱
分类规则: ConnectionError/Timeout/OSError/socket → network;
  ValueError且含'列'/'format'/'解析' → format; KeyError/'无数据'/'empty' → no_data;
  '不存在'/'not found'/'退市' → missing_code; 其余 unknown(默认不重试)
可重试 = network(退避重试) + 显式retryable_types; no_data/missing_code不重试(数据事实)。
"""
from __future__ import annotations

import random
import time

RETRYABLE_NETWORK = (ConnectionError, TimeoutError, OSError)


class RateLimitExceeded(RuntimeError):
    pass


class CircuitOpen(RuntimeError):
    pass


def classify_error(e: Exception) -> str:
    if isinstance(e, RETRYABLE_NETWORK):
        return "network"
    s = f"{type(e).__name__}: {e}"
    low = s.lower()
    if any(k in low for k in ("connection", "timeout", "remotedisconnect", "proxy",
                              "reset by peer", "unreachable")):
        return "network"
    if any(k in s for k in ("列", "format", "解析", "decode", "parse")):
        return "format"
    if isinstance(e, KeyError) or any(k in s for k in ("无数据", "empty", "Empty")):
        return "no_data"
    if any(k in s for k in ("不存在", "not found", "退市", "No such")):
        return "missing_code"
    return "unknown"


class FetchGuard:
    def __init__(self, cap_calls: int = 600, window_s: float = 3600.0,
                 base_backoff_s: float = 2.0, mult: float = 2.0, jitter: float = 0.3,
                 max_retries: int = 3, breaker_threshold: int = 5,
                 breaker_reset_s: float = 900.0, sleeper=time.sleep,
                 clock=time.monotonic, rng=random.Random(7)):
        self.cap, self.window = cap_calls, window_s
        self.base, self.mult, self.jitter, self.max_retries = base_backoff_s, mult, jitter, max_retries
        self.brk_thr, self.brk_reset = breaker_threshold, breaker_reset_s
        self.sleep, self.clock, self.rng = sleeper, clock, rng
        self.calls = []                       # 时间戳(滚动窗)
        self.consec_fail = 0
        self.breaker = "CLOSED"               # CLOSED/OPEN/HALF
        self.opened_at = 0.0
        self.dist = {}                        # 错误分布
        self.first_seen = {}
        self.n_ok = self.n_fail = self.n_retry_sleep = self.n_blocked = 0

    # ---- 上限与熔断闸门 -----------------------------------------------------
    def _gate(self):
        now = self.clock()
        self.calls = [t for t in self.calls if now - t < self.window]
        if len(self.calls) >= self.cap:
            self.n_blocked += 1
            raise RateLimitExceeded(f"cap {self.cap}/{self.window:.0f}s 超限(显式拒绝)")
        if self.breaker == "OPEN":
            if now - self.opened_at >= self.brk_reset:
                self.breaker = "HALF"          # 半开: 放行一次探测
            else:
                self.n_blocked += 1
                raise CircuitOpen(f"熔断OPEN({now - self.opened_at:.0f}s/{self.brk_reset:.0f}s)")

    def call(self, fn, *a, key: str = "", **kw):
        """执行fn并施加全套纪律。返回fn结果; 失败按分类记录并可重试/熔断。"""
        last = None
        for attempt in range(self.max_retries + 1):
            self._gate()
            self.calls.append(self.clock())
            try:
                r = fn(*a, **kw)
            except Exception as e:                     # noqa: BLE001 通道边界: 全收全分类
                cat = classify_error(e)
                self.n_fail += 1
                self.dist[cat] = self.dist.get(cat, 0) + 1
                self.first_seen.setdefault(cat, str(e)[:120])
                last = e
                self.consec_fail += 1
                if self.consec_fail >= self.brk_thr and cat == "network":
                    self.breaker, self.opened_at = "OPEN", self.clock()
                if cat == "network" and attempt < self.max_retries:
                    w = self.base * (self.mult ** attempt) * (1 + self.rng.uniform(-self.jitter, self.jitter))
                    self.n_retry_sleep += 1
                    self.sleep(max(0.0, w))
                    continue
                raise
            else:
                self.n_ok += 1
                self.consec_fail = 0
                if self.breaker == "HALF":
                    self.breaker = "CLOSED"
                return r
        raise last                                     # 理论不达(循环内raise)

    def stats(self) -> dict:
        return {"ok": self.n_ok, "fail": self.n_fail, "retry_sleeps": self.n_retry_sleep,
                "blocked": self.n_blocked, "breaker": self.breaker,
                "error_distribution": dict(self.dist), "first_seen": dict(self.first_seen),
                "rolling_calls": len(self.calls)}


def summarize_failures(missing: list) -> dict:
    """对既有 missing 列表(含 'Type: msg' 文本)做分布归并——forward_ingest扩展层复用。"""
    dist = {}
    for m in missing:
        txt = str(m.get("err") if isinstance(m, dict) else m)
        head = txt.split(":", 1)[0].strip()
        cat = ("network" if any(k in txt.lower() for k in
               ("connection", "timeout", "proxy", "remotedisconnect")) else
               "format" if any(k in txt for k in ("列", "parse", "decode")) else
               "no_data" if "无数据" in txt or "empty" in txt.lower() else
               "missing_code" if "不存在" in txt else "unknown")
        d = dist.setdefault(cat, {"n": 0, "types": {}, "samples": []})
        d["n"] += 1
        d["types"][head] = d["types"].get(head, 0) + 1
        if len(d["samples"]) < 2:
            d["samples"].append(txt[:100])
    return dist
