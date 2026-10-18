# -*- coding: utf-8 -*-
"""阶段E工程测试(§6/§9.1): T+1 / 卖出优先 / 同股双引擎资金单承诺 / 资金守恒 /
缺价显式 / 停牌拒绝 / 风险预算强卖优先。全部走冻结 ashare_executor 真实账本(临时root)。"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from cycle_system.ashare_v2 import config as C                                # noqa: E402
from cycle_system.ashare_v2.portfolio import PortfolioCoordinator             # noqa: E402
from cycle_system.ashare_v2.schemas import Engine, NetTarget                  # noqa: E402

SIG1, EXEC1 = "2026-06-29", "2026-06-30"
SIG2, EXEC2 = "2026-06-30", "2026-07-01"


def _scored(code, pts, engines=None, ind="I1"):
    return {"code": code, "score_points": pts, "engines": engines or [Engine.LONG_CYCLE.value],
            "industry": ind, "record_ids": [1]}


@pytest.fixture()
def coord(tmp_path):
    return PortfolioCoordinator(nav0=C.NAV0, ledger_root=str(tmp_path / "led"), ledger_name="t")


def test_buy_flow_t1_and_conservation(coord):
    net = coord.plan_day(SIG1, [_scored("600001", 9.5)], px={"600001": 10.0},
                         vol20={"600001": 0.02}, ind_of={"600001": "I1"})
    assert isinstance(net, NetTarget) and len(net.buys) == 1 and net.sells == []
    fills, rejects = coord.execute_day(SIG1, px_t1={"600001": 10.2}, prev_close={"600001": 10.0})
    assert len(fills) == 1 and fills[0]["action"] == "BUY" and not rejects
    total = sum(v.shares for v in coord.virtuals)
    assert coord.ledger.pos["600001"]["total"] == total, "虚拟合计=真实持仓(资金单承诺对账)"
    assert coord.ledger.cash > 0
    nav = coord.nav({"600001": 10.2})
    assert abs(nav - (coord.ledger.cash + total * 10.2)) < 1e-6


def test_t1_same_day_sell_structurally_impossible(coord):
    coord.plan_day(SIG1, [_scored("600001", 9.5)], px={"600001": 10.0},
                   vol20={"600001": 0.02}, ind_of={"600001": "I1"})
    coord.execute_day(SIG1, px_t1={"600001": 10.0}, prev_close={"600001": 10.0})
    import pandas as pd
    buy_day = coord.ledger.trading_day
    # 直接伪造"买入当日卖出"成交 → 账本必须拒绝(N2未登记计划/A3绑定不符, sellable未解冻)
    fake = {"plan_hash": "x", "code": "600001", "action": "SELL", "status": "FILLED",
            "execution_date": buy_day, "shares": 100, "price": 10.0, "notional": 1000.0,
            "fees": 5.0, "cash_delta": 995.0, "signal_px": 10.0}
    with pytest.raises(AssertionError):
        coord.ledger.apply(fake, allow_unplanned=False)
    # 合法路径: 信号=买入日(EXEC1=T+1), 卖出计划执行日=下一交易日(EXEC2), 届时T+1解冻可卖
    net2 = coord.plan_day(EXEC1, [], px={"600001": 10.3}, vol20={"600001": 0.02},
                          ind_of={}, exit_ctx_fn=lambda c, d: {"industry_state": "INVALIDATED"})
    assert len(net2.sells) == 1
    fills2, rej2 = coord.execute_day(EXEC1, px_t1={"600001": 10.3}, prev_close={"600001": 10.0})
    assert fills2 and fills2[0]["action"] == "SELL", (fills2, rej2)
    assert "600001" not in coord.ledger.pos and coord.virtuals == []
    assert coord.plans[EXEC1]["exec_day"] == EXEC2


def test_same_code_dual_engine_single_funding(coord):
    s = [_scored("600002", 9.0, [Engine.FAST_V.value, Engine.LONG_CYCLE.value], ind="I2")]
    coord.plan_day(SIG1, s, px={"600002": 5.0}, vol20={"600002": 0.02}, ind_of={"600002": "I2"})
    fills, _ = coord.execute_day(SIG1, px_t1={"600002": 5.0}, prev_close={"600002": 5.0})
    assert len(fills) == 1  # 一条BUY成交
    assert len(coord.virtuals) == 2 and {v.engine for v in coord.virtuals} == {Engine.FAST_V.value,
                                                                                Engine.LONG_CYCLE.value}
    assert sum(v.shares for v in coord.virtuals) == coord.ledger.pos["600002"]["total"]
    # LC引擎退出: 只卖LC虚拟份额
    def ctx(code, day):
        return {"industry_state": "INVALIDATED"}
    net = coord.plan_day(EXEC1, [], px={"600002": 5.1}, vol20={"600002": 0.02},
                         ind_of={}, exit_ctx_fn=ctx)
    lc_v = [v for v in coord.virtuals if v.engine == Engine.LONG_CYCLE.value][0]
    assert sum(x["shares"] for x in net.sells) == lc_v.shares
    coord.execute_day(EXEC1, px_t1={"600002": 5.1}, prev_close={"600002": 5.0})
    fv_v = [v for v in coord.virtuals if v.engine == Engine.FAST_V.value]
    assert len(fv_v) == 1 and coord.ledger.pos["600002"]["total"] == fv_v[0].shares


def test_sells_before_buys_funded_by_proceeds(coord):
    coord.plan_day(SIG1, [_scored("600003", 8.0, ind="I3")], px={"600003": 5.0},
                   vol20={"600003": 0.02}, ind_of={"600003": "I3"})
    coord.execute_day(SIG1, px_t1={"600003": 5.0}, prev_close={"600003": 5.0})
    # 换股: 卖600003 + 买高分600004(预算>剩余现金, 必须靠卖款)
    hold_val = coord.ledger.pos["600003"]["total"] * 5.0
    budget = round(coord.ledger.cash + hold_val * 0.9, 2)
    net = coord.plan_day(EXEC1, [_scored("600004", 9.9, ind="I4")], px={"600004": 10.0},
                         vol20={"600004": 0.02}, ind_of={"600004": "I4"},
                         exit_ctx_fn=lambda c, d: {"industry_state": "INVALIDATED"} if c == "600003" else {})
    assert net.sells and net.buys
    import ashare_executor as ex
    for b in net.buys:
        b.pop("plan", None)                        # 撤销原预算(成交股数=旧预算)
        b["cash_budget"] = budget
        b["plan"] = ex.create_plan("600004", "BUY", EXEC1, 10.0, cash_budget=budget,
                                   created_at=f"{EXEC1}T15:05:00+08:00")
        coord.ledger.register_plan(b["plan"])
    fills, rejects = coord.execute_day(EXEC1, px_t1={"600003": 5.0, "600004": 10.0},
                                       prev_close={"600003": 5.0, "600004": 10.0})
    acts = [f["action"] for f in fills]
    assert acts == ["SELL", "BUY"], f"卖必须先于买入账: {acts}"
    assert not rejects
    assert coord.ledger.cash >= -1e-9 and coord.ledger.cash < budget * 0.2, "买几乎耗尽卖款"


def test_halt_rejected_and_no_state_change(coord):
    coord.plan_day(SIG1, [_scored("600005", 9.0, ind="I5")], px={"600005": 8.0},
                   vol20={"600005": 0.02}, ind_of={"600005": "I5"})
    cash0 = coord.ledger.cash
    fills, rejects = coord.execute_day(SIG1, px_t1={}, prev_close={})
    assert fills == [] and rejects[0]["reason"] == "halted_or_no_price"
    assert coord.ledger.cash == cash0 and coord.virtuals == []
    # 真停牌路径(有价但halted标记): 全新协调器同计划再执行
    coord2 = PortfolioCoordinator(nav0=C.NAV0, ledger_root=os.path.dirname(coord.ledger.log_p),
                                  ledger_name="t2")
    coord2.plan_day(SIG1, [_scored("600005", 9.0, ind="I5")], px={"600005": 8.0},
                    vol20={"600005": 0.02}, ind_of={"600005": "I5"})
    f2, r2 = coord2.execute_day(SIG1, px_t1={"600005": 8.0}, prev_close={"600005": 8.0},
                                halted={"600005"})
    assert f2 == [] and r2[0]["reason"] == "halted_or_no_price"
    assert coord2.ledger.pos == {}


def test_limit_up_rejected_by_executor(coord):
    coord.plan_day(SIG1, [_scored("600006", 9.0, ind="I6")], px={"600006": 10.0},
                   vol20={"600006": 0.02}, ind_of={"600006": "I6"})
    fills, rejects = coord.execute_day(SIG1, px_t1={"600006": 11.0}, prev_close={"600006": 10.0})
    assert fills == [] and rejects[0]["reason"] == "limit_up", "T+1一字涨停冻结执行器必须拒"


def test_risk_budget_exceeded_freeze_and_forced_sell(coord):
    coord.plan_day(SIG1, [_scored("600007", 7.0, ind="I7")], px={"600007": 10.0},
                   vol20={"600007": 0.02}, ind_of={"600007": "I7"})
    coord.execute_day(SIG1, px_t1={"600007": 10.0}, prev_close={"600007": 10.0})
    assert coord.virtuals
    coord.peak_nav = coord.nav({"600007": 10.0}) / (1 - C.RISK_BUDGET_DRAWDOWN - 0.3)  # 制造深回撤历史
    net = coord.plan_day(EXEC1, [_scored("600008", 9.0, ind="I8")], px={"600008": 10.0},
                         vol20={"600008": 0.02}, ind_of={"600008": "I8"},
                         exit_ctx_fn=lambda c, d: {})
    assert net.buys == [], "预算EXCEEDED: 新增全冻结"
    assert net.sells and net.sells[0]["risk_forced"] is True
    assert "EXCEEDED" in net.coverage_note
    fills, _ = coord.execute_day(EXEC1, px_t1={"600007": 9.2}, prev_close={"600007": 10.0})
    assert fills and fills[0]["action"] == "SELL"


def test_missing_price_last_valuation_explicit(coord):
    coord.plan_day(SIG1, [_scored("600009", 9.0, ind="I9")], px={"600009": 10.0},
                   vol20={"600009": 0.02}, ind_of={"600009": "I9"})
    coord.execute_day(SIG1, px_t1={"600009": 10.0}, prev_close={"600009": 10.0})
    nav = coord.nav({})          # 当日无新价 → last-known 显式估值
    assert nav > 0
    coord.ledger.pos["600010"] = {"total": 100, "sellable": 100}
    nav2 = coord.nav({})
    assert any("缺价" in a for a in coord.annotations), "无last-known必须显式标注而非静默"
