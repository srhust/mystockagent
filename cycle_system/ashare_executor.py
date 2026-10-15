# -*- coding: utf-8 -*-
"""Ashare-Stock 统一执行引擎(阶段1): 事前冻结计划 + T+1模拟成交。
铁律: T日收盘生成信号→T+1成交(无泄漏); 涨跌停/停牌拒绝成交; 100股整手; A股费用口径。
纯规则层, 与策略(S/M)解耦——策略只产出事前订单, 本模块负责可成交性与记账。"""
import json
import math
import os


def limit_ratio(code: str, is_st: bool = False) -> float:
    """涨跌幅限制: 创业板(30xxxx)/科创板(688xxx) 20%; ST 5%; 其余主板 10%。"""
    if is_st:
        return 0.05
    if code.startswith("30") or code.startswith("68"):
        return 0.20
    return 0.10


def tradable(code: str, px_t1: float, prev_close: float, halted: bool = False) -> tuple:
    """T+1日可成交判定。返回(ok, reason)。涨跌停/停牌拒绝——买卖双侧都按触及界判定(保守)。"""
    if halted:
        return False, "halted"
    if not (math.isfinite(px_t1) and math.isfinite(prev_close) and prev_close > 0):
        return False, "invalid_price"
    r = limit_ratio(code)
    hi, lo = round(prev_close * (1 + r), 2), round(prev_close * (1 - r), 2)
    if px_t1 >= hi:  # 触及涨停: 买入排板不保证成交, 保守拒绝; 卖出可成交但不假设 — 统一拒绝模拟
        return False, "limit_up"
    if px_t1 <= lo:
        return False, "limit_down"
    return True, "ok"


def round_lot(shares: float) -> int:
    """A股整手: 向下取整到100股; 0=不可买。"""
    return int(shares // 100) * 100


def fees(code: str, notional: float, is_sell: bool) -> float:
    """佣金双边0.025%(最低5元) + 印花税卖出0.05% + 过户费双边0.001%(沪市6xxxx简化全适用)。"""
    commission = max(notional * 0.00025, 5.0)
    stamp = notional * 0.0005 if is_sell else 0.0
    transfer = notional * 0.00001
    return round(commission + stamp + transfer, 2)


def plan_order(code: str, action: str, cash: float, px_ref: float, px_t1: float, prev_close: float,
               halted: bool = False, score: float = 0.0) -> dict:
    """事前冻结订单(T日收盘决策)。px_ref=T日收盘(信号价), px_t1=T+1成交价, prev_close=T日收盘(涨跌停基准)。"""
    assert action in ("BUY", "SELL"), "action必须为BUY/SELL"
    ok, reason = tradable(code, px_t1, prev_close, halted)
    if not ok:
        return {"code": code, "action": action, "status": "REJECTED", "reason": reason, "scoring": False}
    price = px_t1  # T+1实际成交价
    if action == "BUY":
        # 按T+1价估算可买整手(留费用余量), 全仓单票口径由组合层控制
        raw = cash * 0.999 / price
        shares = round_lot(raw)
        if shares <= 0:
            return {"code": code, "action": action, "status": "REJECTED", "reason": "insufficient_lot", "scoring": False}
        notional = shares * price
        while notional + fees(code, notional, False) > cash and shares >= 100:
            shares -= 100
            notional = shares * price
        fee = fees(code, notional, False)
        return {"code": code, "action": action, "status": "FILLED", "shares": shares, "price": price,
                "notional": round(notional, 2), "fees": fee, "cash_delta": -round(notional + fee, 2),
                "signal_px": px_ref, "score": score, "scoring": True}
    shares = round_lot(raw := cash) if False else None  # SELL数量由组合层传入持仓, 此处占位由caller给shares
    return {"code": code, "action": action, "status": "PLAN_ONLY", "scoring": True}


def plan_sell(code: str, shares: int, px_ref: float, px_t1: float, prev_close: float, halted: bool = False) -> dict:
    """事前冻结卖出订单(shares为持有股数, 整手由建仓保证)。"""
    ok, reason = tradable(code, px_t1, prev_close, halted)
    if not ok:
        return {"code": code, "action": "SELL", "status": "REJECTED", "reason": reason, "scoring": False}
    if shares <= 0 or shares % 100 != 0:
        return {"code": code, "action": "SELL", "status": "REJECTED", "reason": "bad_lot", "scoring": False}
    notional = shares * px_t1
    fee = fees(code, notional, True)
    return {"code": code, "action": "SELL", "status": "FILLED", "shares": shares, "price": px_t1,
            "notional": round(notional, 2), "fees": fee, "cash_delta": round(notional - fee, 2),
            "signal_px": px_ref, "scoring": True}


class Ledger:
    """单策略独立账本: NAV/持仓/审计(append-only)。S与M各一个实例, 禁止混账。"""

    def __init__(self, name: str, nav0: float = 100.0, root: str = "output/ashare"):
        self.name = name
        self.cash = nav0
        self.nav0 = nav0
        self.pos = {}  # code -> shares
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.log_p = os.path.join(root, f"ledger_{name}.jsonl")

    def apply(self, fill: dict):
        assert fill.get("status") == "FILLED", f"仅FILLED可入账: {fill}"
        self.cash = round(self.cash + fill["cash_delta"], 4)
        if fill["action"] == "BUY":
            assert self.cash >= -1e-6, f"现金透支: {self.cash}"
            self.pos[fill["code"]] = self.pos.get(fill["code"], 0) + fill["shares"]
        else:
            assert self.pos.get(fill["code"], 0) >= fill["shares"], "卖出超过持仓"
            self.pos[fill["code"]] -= fill["shares"]
            if self.pos[fill["code"]] == 0:
                del self.pos[fill["code"]]
        with open(self.log_p, "a", encoding="utf-8") as f:
            f.write(json.dumps(fill, ensure_ascii=False, default=str) + "\n")

    def nav(self, prices: dict) -> float:
        return round(self.cash + sum(self.pos[c] * prices[c] for c in self.pos if c in prices), 4)
