# -*- coding: utf-8 -*-
"""ca_apply — R1_CAI 集成: 账本级企业行动入账纯函数(预注册语义, 合成测试驱动)。

=== 预注册入账语义(CAI.0-PREREG, 2026-10-19 深夜定稿, 读数前冻结) ===
R1_CAI.1 派息入账时点: ex_date(除权除息日)当日盘后入账(应计制)。理由: raw价格当日
  除息下跌, NAV守恒要求同日入账; pay_date仅记审计行不重复入账。
R1_CAI.2 登记股本: 用 ex_date **前一交易日**收盘后的 ledger 持仓 total(股权登记日语义)。
  total<=0 不产生事件行(NO_HOLDER, 仅审计)。
R1_CAI.3 税档选择(2015-09 差别化公开规则, 按 lot 持有期):
  entry→ex_date 自然日 >365: 免税, after = cash_per_share (=options上界)
  30..365:              10%  , after = 0.9*cash (=options下界, 与baostock双值一致)
  <30:                  20%  , after = 0.8*cash (20%档不在baostock双值中, 显式规则)
  lot 入场日 = trade fills BUY exec_day(FIFO配对); 双值options仅作**交叉校验**
  (与计算档不一致→记 TAX_OPTION_MISMATCH 审计行, 仍以规则档入账——不静默)。
R1_CAI.4 送转/拆股(BONUS): ex_date 当日 total×(1+ratio), 每 lot 虚拟仓位 shares×(1+ratio)
  (向下取整手规则不重做——事件比例保持整手时精确), pos_meta.opened_px÷(1+ratio)
  (每股成本稀释, 实现盈亏语义守恒)。panel adj_factor 同日比率≈1/(1+ratio) 为旁证,
  偏差>1% 记 FACTOR_INCONSISTENT 并保持调整(事件库为准, 面板旁证仅审计)。
R1_CAI.5 守恒: 入账只增 cash(派息)或换算 shares(送转), **不凭空增市值**; 每日审计行落
  ca_events.csv(day/code/type/shares_basis/amount/tax_chosen/source_event), 账本状态
  变化量=审计行之和(扩展 C1: cash=期初-Σ成交+ΣDIVIDEND审计; C3: pos=Σ成交+ΣBONUS股差)。
R1_CAI.6 缺数据期/未验证源事件: 不生成入账(维持CORPORATE_ACTION_UNVERIFIED标注), 本模块
  只消费 status∈{COVERED_*} 文件内 source=baostock.query_dividend_data 的事件。
========================================================================
纯函数(无I/O): apply_dividend / apply_bonus。接线 runner 在 ca_bt_backtest(明日)。
"""
from __future__ import annotations

from datetime import date, datetime


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def tax_tier_after(cash_ps: float, days_held: int, options: list | None) -> tuple:
    """返回 (after_per_share, tier, mismatch_flag)。规则档为准; options 仅交叉校验。"""
    if days_held > 365:
        after, tier = cash_ps, "EXEMPT_GT365"
    elif days_held >= 30:
        after, tier = round(cash_ps * 0.9, 6), "TAX10_30_365"
    else:
        after, tier = round(cash_ps * 0.8, 6), "TAX20_LT30"
    mismatch = False
    if options:
        opts = sorted(float(x) for x in options)
        # 规则档应落在 options 值域内(EXEMPT=上界, TAX10=下界); TAX20 合法低于下界
        if tier == "EXEMPT_GT365" and abs(after - max(opts)) > 1e-6:
            mismatch = True
        elif tier == "TAX10_30_365" and abs(after - min(opts)) > 1e-6:
            mismatch = True
        elif tier == "TAX20_LT30" and not (after < min(opts) + 1e-9):
            mismatch = True
    return after, tier, mismatch


def apply_dividend(cash: float, lots: list, event: dict, ex_prev_day: str) -> tuple:
    """lots: [{"shares":int,"entry_day":"YYYY-MM-DD"}, ...] (FIFO, 登记日语义=已成交)。
    返回 (new_cash, audit_rows)。total 依 R1_CAI.2 = Σlots.shares。"""
    held = sum(int(l["shares"]) for l in lots)
    if held <= 0:
        return cash, [{"day": event["ex_date"], "code": event["code"], "type": "DIVIDEND",
                       "shares_basis": 0, "amount": 0.0, "tax_chosen": "NO_HOLDER",
                       "source_event": event.get("plan_text", "")}]
    rows, total_amt = [], 0.0
    exd = _d(event["ex_date"])
    for l in lots:
        if int(l["shares"]) <= 0:
            continue
        days = (exd - _d(l["entry_day"])).days
        after, tier, mm = tax_tier_after(event["cash_per_share"], days,
                                         l_shares_options(event))
        amt = round(int(l["shares"]) * after, 2)
        total_amt += amt
        rows.append({"day": event["ex_date"], "code": event["code"], "type": "DIVIDEND",
                     "shares_basis": int(l["shares"]), "amount": amt,
                     "tax_chosen": tier, "days_held": days,
                     "source_event": event.get("plan_text", "")})
        if mm:
            rows.append({"day": event["ex_date"], "code": event["code"], "type": "DIVIDEND",
                         "shares_basis": 0, "amount": 0.0, "tax_chosen": "TAX_OPTION_MISMATCH",
                         "source_event": f"rule_tier={tier} options={event.get('cash_after_tax_options')}"})
    return round(cash + total_amt, 2), rows


def l_shares_options(event: dict) -> list | None:
    o = event.get("cash_after_tax_options")
    return [float(x) for x in o] if o else None


def apply_bonus(total: int, lots: list, opened_px_map: dict, event: dict,
                factor_ratio: float | None = None) -> tuple:
    """BONUS/SPLIT: shares×(1+ratio), cost÷(1+ratio)。返回 (new_total, new_lots, rows)。"""
    r = 1.0 + float(event["ratio"])
    new_total = int(total * r)
    new_lots = [{**l, "shares": int(l["shares"] * r)} for l in lots]
    new_px = {k: (v / r if v else v) for k, v in opened_px_map.items()}
    rows = [{"day": event["ex_date"], "code": event["code"], "type": event["type"],
             "shares_basis": total, "amount": round(new_total - total, 4),
             "tax_chosen": f"RATIO_{event['ratio']}", "source_event": event.get("plan_text", "")}]
    if factor_ratio is not None and abs(factor_ratio - r) / r > 0.01:
        rows.append({"day": event["ex_date"], "code": event["code"], "type": event["type"],
                     "shares_basis": 0, "amount": 0.0,
                     "tax_chosen": "FACTOR_INCONSISTENT",
                     "source_event": f"panel_ratio={factor_ratio} event_ratio={r}"})
    return new_total, new_lots, new_px, rows
