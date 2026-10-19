# -*- coding: utf-8 -*-
"""独立验收第4项测试: swap执行化合成场景 — 资金不足触发/卖出被拒级联/部分成交/T+1/跨行业顶。
不改预测规则: edge/成本判据来自冻结allocator; 本组测试验证E层转换与真实账本一致性。"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from cycle_system.ashare_v2 import config as C                    # noqa: E402
from cycle_system.ashare_v2.portfolio import PortfolioCoordinator  # noqa: E402

SIG1, EXEC1 = "2026-06-29", "2026-06-30"
EXEC2 = "2026-07-01"


def _s(code, pts, engines=None, ind="IX"):
    return {"code": code, "score_points": pts, "engines": engines or ["LONG_CYCLE"],
            "industry": ind, "record_ids": [1]}


@pytest.fixture()
def held_coord(tmp_path):
    """先买入低分仓600010(2.0分, px100, 100股), 使次日现金只剩~1手以下的空间。"""
    co = PortfolioCoordinator(nav0=20000, ledger_root=str(tmp_path / "led"), ledger_name="sw")
    co.plan_day(SIG1, [_s("600010", 2.0)], px={"600010": 100.0},
                vol20={"600010": 0.02}, ind_of={"600010": "IX"})
    fills, rej = co.execute_day(SIG1, px_t1={"600010": 100.0}, prev_close={"600010": 100.0})
    assert fills and not rej
    assert co.ledger.pos["600010"]["total"] == 100
    return co


def test_swap_insufficient_cash_triggers_real_out_and_in(held_coord):
    co = held_coord
    # in 一手名义9500 > 剩余spendable → allocator INSUFFICIENT_CASH_FOR_ONE_LOT + swap批准(edge=7)
    net = co.plan_day(EXEC1, [_s("600011", 9.0, ["FAST_V", "LONG_CYCLE"], ind="IY")],
                      px={"600011": 95.0, "600010": 100.0},
                      vol20={"600010": 0.02, "600011": 0.02},
                      ind_of={"600010": "IX", "600011": "IY"},
                      exit_ctx_fn=lambda c, d: {})
    assert any(s["kind"] == "SWAP" and s["code"] == "600010" for s in net.sells)
    assert any(b["code"] == "600011" for b in net.buys)
    swap_row = next(s for s in net.sells if s["kind"] == "SWAP")
    assert swap_row["shares"] == 100
    fills, rej = co.execute_day(EXEC1, px_t1={"600010": 100.0, "600011": 95.0},
                                prev_close={"600010": 100.0, "600011": 95.0})
    assert [f["action"] for f in fills] == ["SELL", "BUY"], "卖out先于买in(卖款养in)"
    assert "600010" not in co.ledger.pos and co.ledger.pos["600011"]["total"] == 100
    vs = {(v.engine, v.code) for v in co.virtuals}
    # 主板100股不可拆两引擎(一手为最小单位): 余数全归engines首位 → 归属必须为声明引擎之一
    assert {v.code for v in co.virtuals} == {"600011"}
    assert all(v.engine in ("FAST_V", "LONG_CYCLE") for v in co.virtuals)
    assert sum(v.shares for v in co.virtuals if v.code == "600011") == 100
    assert co.ledger.cash >= 0 and abs(co.nav({"600011": 95.0}) -
                                       (co.ledger.cash + 100 * 95.0)) < 1e-6


def test_swap_out_sell_rejected_cascades_explicit(held_coord):
    co = held_coord
    net = co.plan_day(EXEC1, [_s("600011", 9.0, ind="IY")], px={"600011": 95.0, "600010": 100.0},
                      vol20={"600010": 0.02, "600011": 0.02},
                      ind_of={"600010": "IX", "600011": "IY"}, exit_ctx_fn=lambda c, d: {})
    assert any(s["kind"] == "SWAP" for s in net.sells)
    # T+1跌停触价 → 执行器拒卖out; 买入侧被剩余现金重估(rebudget), 无负现金/无半仓幻觉
    fills, rej = co.execute_day(EXEC1, px_t1={"600010": 90.0, "600011": 95.0},
                                prev_close={"600010": 100.0, "600011": 95.0})
    assert any(r.get("stage") == "sell" and r["reason"] == "limit_down" for r in rej)
    assert co.ledger.cash >= 0
    assert co.ledger.pos.get("600010", {}).get("total") == 100, "卖被拒则out必须仍完整持有"
    # in 若以剩余现金成交(9997→~90股<1手会被执行器拒) 或不成交: 两者都必须显式记录
    in_fill = [f for f in fills if f["code"] == "600011"]
    if not in_fill:
        assert any(r.get("code") == "600011" for r in rej), "in未成交必须显式拒因"
    vsum = {}
    for v in co.virtuals:
        vsum[v.code] = vsum.get(v.code, 0) + v.shares
    for c, p in co.ledger.pos.items():
        assert vsum.get(c, 0) == p["total"], "虚拟=真实(部分换仓也不破对账)"


def test_swap_in_respects_t_plus_1(held_coord):
    co = held_coord
    net = co.plan_day(EXEC1, [_s("600011", 9.0, ind="IY")], px={"600011": 95.0, "600010": 100.0},
                      vol20={"600010": 0.02, "600011": 0.02},
                      ind_of={"600010": "IX", "600011": "IY"}, exit_ctx_fn=lambda c, d: {})
    co.execute_day(EXEC1, px_t1={"600010": 100.0, "600011": 95.0},
                   prev_close={"600010": 100.0, "600011": 95.0})
    in_pos = co.ledger.pos.get("600011")
    assert in_pos and in_pos["total"] > 0 and in_pos["sellable"] == 0, "swap-in当日不可卖(T+1)"
    # 立即要求EXIT该in: 计划合法生成, 成交只能在下一交易日
    net2 = co.plan_day(EXEC2, [], px={"600011": 96.0}, vol20={"600011": 0.02}, ind_of={},
                       exit_ctx_fn=lambda c, d: {"industry_state": "INVALIDATED"})
    assert any(s["code"] == "600011" for s in net2.sells)
    fills, rej = co.execute_day(EXEC2, px_t1={"600011": 96.0}, prev_close={"600011": 95.0})
    assert fills and "600011" not in co.ledger.pos, "T+1解冻后方可卖出"


def test_swap_blocked_by_industry_cap_explicit(held_coord):
    co = held_coord
    # in与持仓同行业IX且行业占用已≈顶(100股*100=10000 → 余量=12000-10000=2000 < 一手9500)
    net = co.plan_day(EXEC1, [_s("600012", 9.0, ind="IX")], px={"600012": 95.0, "600010": 100.0},
                      vol20={"600010": 0.02, "600012": 0.02},
                      ind_of={"600010": "IX", "600012": "IX"}, exit_ctx_fn=lambda c, d: {})
    assert not any(s.get("kind") == "SWAP" and s["code"] == "600010" for s in net.sells) \
        or any("swap-in不足一手跳过" in a or "超组合风险" in a for a in co.annotations), \
        "行业60%硬顶下swap必须被截断或显式跳过(B2锁)"
    # 无论哪条路径, swap-in成交预算不得超过行业余量:
    for b in net.buys:
        if b["code"] == "600012":
            assert b["cash_budget"] <= 2000 + 2000 * 0.0003 + 6 + 1e-6


def test_swap_priority_order_below_exit_and_forced(held_coord):
    co = held_coord
    # EXIT持仓600010(INVALIDATED) + 高分in → 卖款充足则直接买入(无SWAP行), sells顺序合法;
    # 若allocator在持仓仍计入时判swap, out已在退出队列 → swap跳过(annotation去重显式)。
    net = co.plan_day(EXEC1, [_s("600011", 9.5, ind="IY")],
                      px={"600011": 95.0, "600010": 100.0},
                      vol20={"600010": 0.02, "600011": 0.02},
                      ind_of={"600010": "IX", "600011": "IY"},
                      exit_ctx_fn=lambda c, d: {"industry_state": "INVALIDATED"})
    kinds = [s.get("kind") for s in net.sells]
    assert kinds and kinds[0] == "EXIT", f"引擎退出必须最先: {kinds}"
    ids = [s["pos_id"] for s in net.sells]
    assert len(ids) == len(set(ids)), "同一虚拟持仓不得重复卖出(out去重锁)"
    fills, rej = co.execute_day(EXEC1, px_t1={"600010": 100.0, "600011": 95.0},
                                prev_close={"600010": 100.0, "600011": 95.0})
    acts = [f["action"] for f in fills]
    assert acts[0] == "SELL", "真实入账: 卖先于买"
    total_sell = sum(f["shares"] for f in fills if f["action"] == "SELL")
    assert total_sell == 100, "同一持仓只卖一次(去重后无重复卖出)"
    assert co.ledger.cash >= 0
