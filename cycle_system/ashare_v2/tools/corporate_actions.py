# -*- coding: utf-8 -*-
"""corporate_actions — 包D: 企业行动机制与数据可用性审计(裁定D-1~D-4)。

背景(裁定原文): "真实送转股需要调整实际持股数量, 现金分红需要入账, 不能因为复权
因子存在微漂就完全取消企业行动处理"。同时adj factor drift(95.6%行)证明**因子不可
作每日转换驱动**; 本模块用**事件级**处理: 仅对经接口验证的分红/送转/拆股/配股/退市
事件, 在**生效日(ex-date)**按**实际比例**调整股数/入账现金; 无事件数据的券保持
CORPORATE_ACTION_UNVERIFIED 标记(净值仍不可称完整总收益)。

API面:
  apply_day_events(state, events_on_ex_date, registry) -> (new_state, applied, unverified)
    state = {"cash": float, "positions": {code: int_shares}}
    events = [{"code","type": BONUS|SPLIT|DIVIDEND|RIGHTS|DELIST, "ex_date",
               "ratio"(送转/拆每1股新增), "cash_per_share", "rights_price",
               "subscribed_ratio", "source"}]
    registry = 已知数据源可信度登记; 事件缺关键字段/来源不可信 → 不应用, 记unverified
  audit_availability(codes) -> 接口存在性+抓取状态报告(网络受限期间返回UNVERIFIED降级)

守恒不变量(测试锁定):
  BONUS/SPLIT: 市值连续性 shares_pre×px_pre = shares_post×px_theo_post (理论除权价)
  DIVIDEND:    shares不变; cash += shares×dps; 市值降 shares×(px_pre−dps_theo)
  RIGHTS:      持有人财富守恒(缴款换股, 按认购量)
  DELIST:      按最后可得价一次性出清入账或标SUSPENDED_VALUATION
  全局: Σ(cash+市值) 除分红入账/配股缴款外逐事件守恒, 记账误差<0.01
"""
from __future__ import annotations

EVENT_TYPES = ("BONUS", "SPLIT", "DIVIDEND", "RIGHTS", "DELIST")
TRUSTED_SOURCES = ("stock_history_dividend_detail:em", "stock_dividend_cninfo")


def _round_shares(x: float) -> int:
    # 送转股到手为整数股(10送4: 100股+40); 尾股截断入账显式返回
    return int(x)


def apply_day_events(state: dict, events: list, registry: dict | None = None) -> tuple:
    """返回 (new_state, applied_rows, unverified_rows)。纯函数, 不触碰账本对象。"""
    reg = registry or {"trusted_sources": TRUSTED_SOURCES}
    cash = float(state["cash"])
    pos = dict(state["positions"])
    applied, unver = [], []
    for ev in events:
        code, typ = str(ev.get("code")), ev.get("type")
        src_ok = str(ev.get("source", "")) in reg["trusted_sources"]
        sh = pos.get(code, 0)
        if typ not in EVENT_TYPES or not src_ok or sh <= 0:
            unver.append({"code": code, "type": typ, "ex_date": ev.get("ex_date"),
                          "reason": ("UNTRUSTED_SOURCE" if not src_ok else
                                     "NO_POSITION" if sh <= 0 else "UNKNOWN_TYPE")})
            continue
        if typ in ("BONUS", "SPLIT"):
            r = float(ev.get("ratio", 0.0))
            if r <= 0:
                unver.append({"code": code, "type": typ, "ex_date": ev.get("ex_date"),
                              "reason": "RATIO_MISSING"})
                continue
            pre = sh
            post = _round_shares(sh * (1.0 + r))
            pos[code] = post
            applied.append({"code": code, "type": typ, "ex_date": ev.get("ex_date"),
                            "shares_pre": pre, "shares_post": post,
                            "frac_dropped": sh * (1.0 + r) - post})
        elif typ == "DIVIDEND":
            dps = float(ev.get("cash_per_share", 0.0))
            tax = float(ev.get("tax_rate", 0.0))
            if dps <= 0:
                unver.append({"code": code, "type": typ, "ex_date": ev.get("ex_date"),
                              "reason": "DPS_MISSING"})
                continue
            gross = sh * dps
            net = gross * (1.0 - tax)
            cash += net
            applied.append({"code": code, "type": typ, "ex_date": ev.get("ex_date"),
                            "shares_pre": sh, "shares_post": sh,
                            "cash_delta": round(net, 4), "gross": round(gross, 4),
                            "tax_withheld": round(gross - net, 4)})
        elif typ == "RIGHTS":
            sub = float(ev.get("subscribed_ratio", 1.0))
            price = float(ev.get("rights_price", 0.0))
            r = float(ev.get("ratio", 0.0))
            if price <= 0 or r <= 0:
                unver.append({"code": code, "type": typ, "ex_date": ev.get("ex_date"),
                              "reason": "RIGHTS_TERMS_MISSING"})
                continue
            add = _round_shares(sh * r * sub)
            cost = add * price
            if cost > cash + 1e-9:      # 现金不足不强制透支: 部分认购
                add = _round_shares(cash / price)
                cost = add * price
            pos[code] = sh + add
            cash -= cost
            applied.append({"code": code, "type": typ, "ex_date": ev.get("ex_date"),
                            "shares_pre": sh, "shares_post": sh + add,
                            "cash_delta": round(-cost, 4)})
        elif typ == "DELIST":
            px = ev.get("final_price")
            if px is None:
                unver.append({"code": code, "type": typ, "ex_date": ev.get("ex_date"),
                              "reason": "SUSPENDED_VALUATION(终价缺失)"})
                continue
            cash += sh * float(px)
            del pos[code]
            applied.append({"code": code, "type": typ, "ex_date": ev.get("ex_date"),
                            "shares_pre": sh, "shares_post": 0,
                            "cash_delta": round(sh * float(px), 4)})
    return {"cash": round(cash, 4), "positions": pos}, applied, unver


def audit_availability(codes: list) -> dict:
    """事件数据可用性审计(网络受限期=登记接口+UNVERIFIED降级, 不实抓)。"""
    return {
        "_class": "CORPORATE_ACTION_DATA_AVAILABILITY / 机制先行, 抓取待网络恢复",
        "interfaces_registered": [
            {"fn": "akshare.stock_history_dividend_detail", "vendor": "东财",
             "covers": "分红送配/拆股明细(含除权除息日)", "status": "EXISTS_OFFLINE_VERIFIED"},
            {"fn": "akshare.stock_dividend_cninfo", "vendor": "巨潮",
             "covers": "分红派息实施公告", "status": "EXISTS_OFFLINE_VERIFIED"},
            {"fn": "akshare.stock_history_dividend", "vendor": "新浪",
             "covers": "历史分红概要", "status": "EXISTS_OFFLINE_VERIFIED"},
            {"fn": "baostock(扩展项目)", "vendor": "官方",
             "covers": "分红送转/股份变动/退市(2000扩展并行项)", "status": "PLANNED"}],
        "fetch_status": "SUSPENDED: SUSPECTED_RATE_LIMIT_OR_CONNECTION_REJECTION(见AUDIT_FINAL §5), "
                        "实拉在数据通道恢复后按请求上限+退避+熔断纪律执行",
        "n_codes_queued": len(codes),
        "ledger_marking_rule": "无经验证事件数据的券×区间 → CORPORATE_ACTION_UNVERIFIED; "
                               "整体净值不得称完整总收益(裁定D-4)",
    }
