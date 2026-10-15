# -*- coding: utf-8 -*-
"""Ashare-Stock 统一执行引擎(阶段1 R2): FrozenPlan(T日,零T+1信息) → ExecutionResult(T+1)。
铁律: 计划与成交两阶段分离(计划无法访问T+1行情); 涨跌停/停牌拒绝; 整手(科创板200股起); A股费用; T+1可卖约束; 账本原子性。"""
import hashlib
import json
import os

import pandas as pd

ST_RULE_NEW = pd.Timestamp("2026-07-06")  # 仅主板ST 5%→10%
GEM_REG = pd.Timestamp("2020-08-24")  # 创业板注册制: 普通股10%→20%, ST恒20%


def limit_ratio(code: str, trade_date, is_st: bool = False) -> float:
    """按交易日分期的涨跌幅: 创业板(30)/科创板(68) 20%; 主板 10%; ST按新旧规(2026-07-06前主板/创业板ST均5%)。"""
    d = pd.Timestamp(trade_date)
    if code.startswith("30"):  # 创业板: 注册制(2020-08-24)前普通10%/ST5%; 后恒20%(A1)
        if d < GEM_REG:
            return 0.05 if is_st else 0.10
        return 0.20
    if code.startswith("68"):
        return 0.20
    return 0.10 if (not is_st or d >= ST_RULE_NEW) else 0.05  # 主板/主板ST(2026-07-06分期)


def lot_step(code: str) -> tuple:
    """申报数量规则: 科创板(68) 200股起+1股递增; 其余 100股整手。"""
    return (200, 1) if code.startswith("68") else (100, 100)


def round_lot(code: str, budget_shares: float) -> int:
    """按板块申报规则向下取整。"""
    base, step = lot_step(code)
    n = int(budget_shares)
    if n < base:
        return 0
    return base + ((n - base) // step) * step


def tradable(code: str, px_t1: float, prev_close: float, trade_date, halted: bool = False, is_st: bool = False) -> tuple:
    """T+1日可成交判定(保守: 触及涨跌停即拒)。"""
    if halted:
        return False, "halted"
    if not (pd.notna(px_t1) and pd.notna(prev_close) and prev_close > 0):
        return False, "invalid_price"
    r = limit_ratio(code, trade_date, is_st)
    hi, lo = round(prev_close * (1 + r), 2), round(prev_close * (1 - r), 2)
    if px_t1 >= hi:
        return False, "limit_up"
    if px_t1 <= lo:
        return False, "limit_down"
    return True, "ok"


def fees(code: str, notional: float, is_sell: bool) -> float:
    """佣金双边0.025%(最低5元) + 印花税卖出0.05% + 过户费双边0.001%。"""
    return round(max(notional * 0.00025, 5.0) + (notional * 0.0005 if is_sell else 0.0) + notional * 0.00001, 2)


def create_plan(code: str, action: str, trade_date, px_ref: float, cash_budget: float = None, shares: int = None, created_at: str = None) -> dict:
    """阶段一: T日收盘冻结计划。签名中不存在任何T+1行情——时序无泄漏由构造保证。created_at仅测试注入合法。"""
    assert action in ("BUY", "SELL")
    assert (action == "BUY") == (cash_budget is not None), "BUY必须带cash_budget; SELL必须带shares"
    assert shares is None or (shares > 0), "卖出股数须为正"
    plan = {"code": code, "action": action, "trade_date": str(pd.Timestamp(trade_date).date()),
            "signal_px": float(px_ref), "created_at": created_at or pd.Timestamp.now(tz="UTC").isoformat()}
    if action == "BUY":
        plan["cash_budget"] = float(cash_budget)
    else:
        plan["shares"] = int(shares)
    body = json.dumps(plan, sort_keys=True, ensure_ascii=False)
    plan["plan_hash"] = hashlib.sha1(body.encode()).hexdigest()[:12]
    return plan


def _next_bdate(d):
    x = pd.Timestamp(d).normalize() + pd.Timedelta(days=1)
    while x.weekday() >= 5:
        x += pd.Timedelta(days=1)
    return x  # 简化A股日历(仅周末), 法定节假日接入待数据管道(P1)


def execute_plan(plan: dict, trade_date_t1, px_t1: float, prev_close: float, halted: bool = False, is_st: bool = False, allow_residual_sell: bool = False) -> dict:
    """阶段二: T+1消费冻结计划。验哈希→执行日必须=计划日下一交易日(A2)→可成交性→整手/费用。"""
    body = json.dumps({k: v for k, v in plan.items() if k != "plan_hash"}, sort_keys=True, ensure_ascii=False)
    assert hashlib.sha1(body.encode()).hexdigest()[:12] == plan["plan_hash"], "计划哈希校验失败: 计划被篡改"
    assert pd.Timestamp(trade_date_t1).normalize() == _next_bdate(plan["trade_date"]), "A2: 执行日必须为计划日的下一交易日, 禁止同日成交"
    ca = pd.Timestamp(plan["created_at"]).tz_convert("Asia/Shanghai").tz_localize(None)  # A2窗口: 北京时间[计划日15:00, 执行日开盘)
    assert ca >= pd.Timestamp(plan["trade_date"]) + pd.Timedelta(hours=15) and ca.date() <= pd.Timestamp(trade_date_t1).date(), "A2: 计划生成时间必须于计划日收盘后、执行日开盘前窗口内"
    code, action = plan["code"], plan["action"]
    ok, reason = tradable(code, px_t1, prev_close, trade_date_t1, halted, is_st)
    rej = {"plan_hash": plan["plan_hash"], "code": code, "action": action, "status": "REJECTED",
           "reason": reason, "signal_px": plan["signal_px"], "scoring": False}
    if not ok:
        return rej
    price = float(px_t1)
    if action == "BUY":
        raw = plan["cash_budget"] * 0.999 / price
        shares = round_lot(code, raw)
        while shares > 0:
            notional = shares * price
            fee = fees(code, notional, False)
            if notional + fee <= plan["cash_budget"]:
                break
            shares = shares - lot_step(code)[1] if shares > lot_step(code)[0] else 0
        if shares <= 0:  # P0-1: 费用调整后必须复检, 零股成交严禁扣费
            return {**rej, "reason": "insufficient_cash_after_fees"}
        notional = shares * price
        fee = fees(code, notional, False)
        return {"plan_hash": plan["plan_hash"], "code": code, "action": "BUY", "status": "FILLED",
                "shares": shares, "price": price, "notional": round(notional, 2), "fees": fee,
                "cash_delta": -round(notional + fee, 2), "signal_px": plan["signal_px"], "scoring": True}
    shares = plan["shares"]
    base, step = lot_step(code)
    if shares < base and not (allow_residual_sell and code.startswith("68") and shares > 0):
        return {**rej, "reason": "bad_lot"}  # A5: 科创板余额<200一次性卖出由账本验证total==shares
    notional = shares * price
    fee = fees(code, notional, True)
    return {"plan_hash": plan["plan_hash"], "code": code, "action": "SELL", "status": "FILLED",
            "shares": shares, "price": price, "notional": round(notional, 2), "fees": fee,
            "cash_delta": round(notional - fee, 2), "signal_px": plan["signal_px"], "scoring": True}


class Ledger:
    """单策略独立账本: 原子apply(先验证后提交, 异常零副作用) + A股T+1可卖约束 + append-only审计。"""

    def __init__(self, name: str, nav0: float = 20000.0, root: str = "output/ashare"):
        self.name, self.nav0 = name, nav0
        self.cash = float(nav0)
        self.pos = {}  # code -> {"total": int, "sellable": int}  (sellable=当日可卖, 买入当日不增)
        self.trading_day = None
        os.makedirs(root, exist_ok=True)
        self.log_p = os.path.join(root, f"ledger_{name}.jsonl")

    def advance_day(self, day):
        """交易日切换(严格前进, A4): 全部持仓转为可卖(完成T+1)。同日重复调用或倒退拒绝。"""
        d = str(pd.Timestamp(day).date())
        assert self.trading_day is None or d > self.trading_day, "A4: 交易日必须严格前进, 禁止同日重复切换或倒退"
        self.trading_day = d
        for c in self.pos:
            self.pos[c]["sellable"] = self.pos[c]["total"]

    def apply(self, fill: dict):
        """原子入账(A3): 独立不变量——正股数/金额守恒/唯一成交身份(plan_hash)/可卖约束→写审计→提交。"""
        assert fill.get("status") == "FILLED" and fill.get("action") in ("BUY", "SELL"), f"仅FILLED可入账: {fill}"
        ph = fill.get("plan_hash")
        assert ph and ph not in getattr(self, "_executed", set()), f"A3: 重复成交拒绝: {ph}"
        code, shares, delta = fill["code"], fill["shares"], fill["cash_delta"]
        assert isinstance(shares, int) and not isinstance(shares, bool) and shares > 0, f"A3: 股数必须为正整数: {shares}"
        assert fill.get("price", 0) > 0 and fill.get("notional") is not None and fill.get("fees") is not None, "A3: 成交记录不完整"
        assert round(shares * fill["price"], 2) == round(fill["notional"], 2), "A3: 金额守恒失败(notional != shares*price)"
        expect = -(round(fill["notional"], 2) + fill["fees"]) if fill["action"] == "BUY" else round(fill["notional"], 2) - fill["fees"]
        assert abs(delta - expect) < 0.01, f"A3: 资金守恒失败(cash_delta与notional/fees不符): {delta} vs {expect}"
        new_cash = round(self.cash + delta, 4)
        new_pos = {c: dict(v) for c, v in self.pos.items()}
        if fill["action"] == "BUY":
            assert new_cash >= -1e-6, f"现金透支: {new_cash}"
            p = new_pos.setdefault(code, {"total": 0, "sellable": 0})
            p["total"] += shares  # 当日买入不可卖(T+1)
        else:
            p = new_pos.get(code)
            assert p is not None and p["sellable"] >= shares, f"卖出超过可卖数量: {code} sellable={p['sellable'] if p else 0} req={shares}"
            if shares < lot_step(code)[0] and code.startswith("68"):
                assert p["total"] == shares, "A5: 科创板不足200股仅允许一次性卖出全部余额"
            p["total"] -= shares
            p["sellable"] -= shares
            if p["total"] == 0:
                del new_pos[code]
        with open(self.log_p, "a", encoding="utf-8") as f:  # 验证全过后才持久化
            f.write(json.dumps(fill, ensure_ascii=False, default=str) + "\n")
        self.cash, self.pos = new_cash, new_pos
        self._executed = getattr(self, "_executed", set()) | {ph}

    def nav(self, prices: dict, missing_action: str = "error") -> float:
        """净值: 缺价持仓必须显式处理(error抛出/last估值并标记), 禁止静默忽略。"""
        total = self.cash
        missing = [c for c in self.pos if c not in prices]
        if missing and missing_action == "error":
            raise KeyError(f"持仓缺价, 拒绝静默估值: {missing}")
        for c in self.pos:
            total += self.pos[c]["total"] * prices[c]
        return round(total, 4)
