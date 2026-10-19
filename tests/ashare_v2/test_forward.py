# -*- coding: utf-8 -*-
"""独立验收第6项测试: 不可篡改归档链 + run_kind前向/回放判定 + 协调器状态跨进程重建。"""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from cycle_system.ashare_v2 import config as C                                # noqa: E402
from cycle_system.ashare_v2 import forward_ledger as FL                       # noqa: E402
from cycle_system.ashare_v2.portfolio import PortfolioCoordinator             # noqa: E402


def test_chain_append_verify_tamper(tmp_path):
    p = str(tmp_path / "chain.jsonl")
    for i in range(5):
        FL.append_record(p, {"signal_day": f"2026-07-{10+i:02d}", "n": i})
    assert FL.verify(p) == {"ok": True, "n": 5, "broken_at": None}
    # 篡改第3行 payload
    lines = open(p, encoding="utf-8").read().splitlines()
    d = json.loads(lines[2]); d["n"] = 99
    lines[2] = json.dumps(d, ensure_ascii=False)
    open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    v = FL.verify(p)
    assert not v["ok"] and v["broken_at"]["line"] == 3
    # 删除第2行(插入/删除检测)
    open(p, "w", encoding="utf-8").write("\n".join([lines[0]] + lines[2:]) + "\n")
    assert not FL.verify(p)["ok"]


def test_run_kind_forward_vs_replay(tmp_path):
    p = str(tmp_path / "c.jsonl")
    fr = {"frozen_trade_date": "2026-10-20"}
    assert FL.append_record(p, {"signal_day": "2026-10-21"}, fr)["run_kind"] == "FORWARD"
    assert FL.append_record(p, {"signal_day": "2026-10-19"}, fr)["run_kind"] == "REPLAY"
    assert FL.append_record(p, {"signal_day": "2026-10-21"}, None)["run_kind"] == "REPLAY", "无锚保守判REPLAY"
    assert FL.append_record(p, {"signal_day": "2026-10-21"}, {"frozen_trade_date": "2026-10-20"})["run_kind"] == "FORWARD"
    same_day = FL.append_record(p, {"signal_day": "2026-10-20"}, fr)["run_kind"]
    assert same_day == "REPLAY", "冻结当日不高于冻结日→REPLAY(严格大于才算前向)"


def test_coordinator_state_roundtrip(tmp_path):
    root = str(tmp_path / "led")
    co = PortfolioCoordinator(nav0=C.NAV0, ledger_root=root, ledger_name="fwd")
    co.plan_day("2026-06-29", [{"code": "600001", "score_points": 9.0,
                                "engines": ["LONG_CYCLE"], "industry": "I", "record_ids": [1]}],
                px={"600001": 10.0}, vol20={"600001": 0.02}, ind_of={"600001": "I"})
    fills, _ = co.execute_day("2026-06-29", px_t1={"600001": 10.0}, prev_close={"600001": 10.0})
    assert fills
    sp = str(tmp_path / "state.json")
    co.save_state(sp)
    co2 = PortfolioCoordinator.load_state(sp, nav0=C.NAV0, ledger_root=root, ledger_name="fwd")
    assert co2.ledger.cash == co.ledger.cash
    assert co2.ledger.pos == co.ledger.pos
    assert [(v.position_id, v.code, v.engine, v.shares) for v in co2.virtuals] == \
        [(v.position_id, v.code, v.engine, v.shares) for v in co.virtuals]
    assert co2.px_last == co.px_last and co2.peak_nav == co.peak_nav
    # 重建后继续下一日流程: 卖出LC虚拟份额并真实入账(计划仍在, plan可注册)
    net = co2.plan_day("2026-06-30", [], px={"600001": 10.5}, vol20={"600001": 0.02}, ind_of={},
                       exit_ctx_fn=lambda c, d: {"industry_state": "INVALIDATED"})
    assert len(net.sells) == 1
    f2, r2 = co2.execute_day("2026-06-30", px_t1={"600001": 10.5}, prev_close={"600001": 10.0})
    assert f2 and not r2 and "600001" not in co2.ledger.pos


def test_chain_integration_with_plan_execute(tmp_path, monkeypatch):
    """forward_ledger + 协调器: 归档两条记录后链校验OK, run_kind标注齐全。"""
    p = str(tmp_path / "chain.jsonl")
    root = str(tmp_path / "led")
    co = PortfolioCoordinator(nav0=C.NAV0, ledger_root=root, ledger_name="fwd2")
    co.plan_day("2026-06-29", [{"code": "600002", "score_points": 8.0,
                                "engines": ["FAST_V"], "industry": "J", "record_ids": [2]}],
                px={"600002": 5.0}, vol20={"600002": 0.03}, ind_of={"600002": "J"})
    FL.append_record(p, {"event": "PLAN", "signal_day": "2026-06-29",
                         "sells": [], "buys": [{"code": "600002", "budget": 5000}]},
                     freeze={"frozen_trade_date": "2026-06-20"})
    fills, _ = co.execute_day("2026-06-29", px_t1={"600002": 5.0}, prev_close={"600002": 5.0})
    FL.append_record(p, {"event": "EXECUTE", "signal_day": "2026-06-29", "exec_day": "2026-06-30",
                         "fills_n": len(fills)}, freeze={"frozen_trade_date": "2026-06-20"})
    v = FL.verify(p)
    assert v["ok"] and v["n"] == 2
    assert all(x["run_kind"] == "FORWARD" for x in FL.read_all(p))
