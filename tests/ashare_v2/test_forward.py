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


# ══ 第二轮复核P0负例(执行入口修复锁定) ══════════════════════════════
import pandas as pd                                                # noqa: E402
from datetime import datetime                                      # noqa: E402
import cycle_system.ashare_v2.forward_ingest as FI                 # noqa: E402
import cycle_system.ashare_v2.tools.forward_shadow as FS           # noqa: E402


def _synth_panel(codes, end="2026-10-15", n=320):
    dates = pd.bdate_range(end=end, periods=n)
    rows = []
    for i, code in enumerate(codes):
        base = 10.0 + i
        px = [base * (1 + 0.0002 * k + (0.02 if k > n - 8 else 0)) for k in range(n)]
        for d, p in zip(dates, px):
            rows.append({"date": d, "code": code, "sector": "I", "open": p * 0.999,
                         "high": p * 1.001, "low": p * 0.998, "close": p,
                         "volume": 1e7, "amount": 2e8, "adj_factor": 1.0, "close_adj": p})
    return pd.DataFrame(rows)


def _fwd_env(tmp_path, monkeypatch, panel):
    monkeypatch.setattr(FS, "FWD_DIR", str(tmp_path / "fwd"))
    os.makedirs(tmp_path / "fwd", exist_ok=True)
    monkeypatch.setattr(FS, "CHAIN_P", str(tmp_path / "fwd" / "chain.jsonl"))
    monkeypatch.setattr(FS, "HEAD_P", str(tmp_path / "fwd" / "head.json"))
    monkeypatch.setattr(FS, "STATE_P", str(tmp_path / "fwd" / "state.json"))
    monkeypatch.setattr(FS, "FREEZE_P", str(tmp_path / "fwd" / "FREEZE.json"))
    monkeypatch.setattr(FS, "ADMIT_P", str(tmp_path / "fwd" / "ADMIT.json"))
    monkeypatch.setattr(FI, "ING_DIR", str(tmp_path / "fwd" / "incremental"))
    os.makedirs(FI.ING_DIR, exist_ok=True)
    json.dump({"frozen_trade_date": "2026-10-14", "module_hashes": {}, "version": "T.R0"},
              open(tmp_path / "fwd" / "FREEZE.json", "w", encoding="utf-8"))
    monkeypatch.setattr(FS, "_merged_portal",
                        lambda through: (panel, "2026-10-14", ["2026-10-15"]))
    monkeypatch.setattr(FI, "require_plan_ready", lambda day: {
        "fetched_at": f"{day}T15:30:00+08:00", "source": "test", "csv_sha256": "x" * 64,
        "codes_missing": []})


def test_p03_rebudget_only_shrinks_with_parent_link(tmp_path):
    """P0-3: T+1现金约束只许缩减, 调整记录带父plan哈希且单调/换码不可能。"""
    root = str(tmp_path / "led")
    co = PortfolioCoordinator(nav0=C.NAV0, ledger_root=root, ledger_name="rb")
    co.plan_day("2026-06-29", [{"code": "600010", "score_points": 9.0,
                                "engines": ["LONG_CYCLE"], "industry": "I", "record_ids": ["r"]}],
                px={"600010": 10.0}, vol20={"600010": 0.02}, ind_of={"600010": "I"})
    rec = co.plans["2026-06-29"]
    assert rec["buys"], "计划应有买入"
    orig_hash = rec["buys"][0]["plan"]["plan_hash"]
    orig_budget = rec["buys"][0]["cash_budget"]
    co.ledger.cash = 500.0                        # 极端缺现金(卖款未到账模拟)
    co._rebudget_pending_buys(rec, "2026-06-29")
    b = rec["buys"][0]
    assert b["cash_budget"] <= 500.0 < orig_budget, "只许缩减"
    adj = co.adjust_log[-1]
    assert adj["parent_plan_hash"] == orig_hash and adj["budget_before"] == orig_budget
    assert adj["budget_after"] == b["cash_budget"] and adj["monotone_down"] is True
    assert adj["code_unchanged"] is True and adj["code"] == "600010"
    # 只减不增: 再喂更多现金也不得回涨
    co.ledger.cash = 99999.0
    co._rebudget_pending_buys(rec, "2026-06-29")
    assert b["cash_budget"] <= 500.0, "预算不得回升"
    assert len([a for a in co.adjust_log if a["code"] == "600010"]) == 1, "无二次调整"


def test_p01_forward_plan_execute_uses_raw_open_and_seals(tmp_path, monkeypatch):
    """P0-1/P0-2+纪律: plan→FORWARD标注+DIAGNOSTIC门; execute用增量raw open延迟重建; 幂等拒绝重复。"""
    codes = ["600010", "600011", "600012"]
    panel = _synth_panel(codes)
    _fwd_env(tmp_path, monkeypatch, panel)
    rc = FS.cmd_plan("2026-10-15")
    assert rc == 0
    recs = FL.read_all(FS.CHAIN_P)
    plan_rec = recs[-1]
    assert plan_rec["run_kind"] == "FORWARD", "信号日>锚=FORWARD"
    assert plan_rec["formal_gate"] == "DIAGNOSTIC_REHEARSAL", "默认非正式账本门"
    assert plan_rec["data_fetched_at"].endswith("+08:00") and plan_rec["data_csv_sha256"]
    assert FS.cmd_plan("2026-10-15") == 3, "幂等: 同日重复plan拒绝"
    head = json.load(open(FS.HEAD_P, encoding="utf-8"))
    assert head["ok"] and head["seq"] == 1 and head["hash"]
    # 增量层exec日raw open: 与close不同 — 成交价必须=open非close
    day_px = panel[panel["date"] == pd.Timestamp("2026-10-15")].set_index("code")
    inc = panel[panel["date"] == pd.Timestamp("2026-10-14")].copy()
    inc["date"] = pd.Timestamp("2026-10-16")
    inc["open"] = day_px["close"].values * 1.02
    inc["close"] = day_px["close"].values * 1.05
    inc_by_day = {"2026-10-16": inc, "2026-10-15": panel[panel["date"] == pd.Timestamp("2026-10-15")]}
    monkeypatch.setattr(FI, "load_day", lambda d: inc_by_day[d])
    json.dump({"fetched_at": "2026-10-16T15:30:00+08:00"},
              open(os.path.join(FI.ING_DIR, "2026-10-16.meta.json"), "w", encoding="utf-8"))
    rc2 = FS.cmd_execute("2026-10-16")
    assert rc2 == 0, "execute应成功(收盘后重建)"
    ex_rec = FL.read_all(FS.CHAIN_P)[-1]
    assert ex_rec["execution_mode"] == "DELAYED_REBUILD_RAW_OPEN"
    assert ex_rec["formal_gate"] == "DIAGNOSTIC_REHEARSAL"
    inc_open = inc.set_index("code")["open"]
    for f in ex_rec["fills"]:
        assert abs(f["price"] - float(inc_open[f["code"]])) < 1e-9, "成交价=raw open非close"
    assert FS.cmd_execute("2026-10-16") == 3, "幂等: 重复execute拒绝"


def test_p01_execute_rejects_intraday(tmp_path, monkeypatch):
    """时间纪律: 当日重建必须在15:00收盘后, 盘中直接拒绝(rc=5)。"""
    codes = ["600020"]
    panel = _synth_panel(codes, end="2026-10-15")
    _fwd_env(tmp_path, monkeypatch, panel)
    assert FS.cmd_plan("2026-10-15") == 0

    class _FakeDT:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 10, 16, 10, 0, tzinfo=FS.TZ)

    monkeypatch.setattr(FS, "datetime", _FakeDT)
    assert FS.cmd_execute("2026-10-16") == 5, "盘中(10:00)重建拒绝"


def test_missing_increment_graceful_rc6(tmp_path, monkeypatch):
    """P0-2负例: 增量层缺日 → plan/execute均给出结构化拒绝(rc=6), 不裸抛。"""
    codes = ["600040"]
    panel = _synth_panel(codes, end="2026-10-15")
    _fwd_env(tmp_path, monkeypatch, panel)
    monkeypatch.setattr(FI, "require_plan_ready",
                        lambda day: (_ for _ in ()).throw(FileNotFoundError(f"增量层无{day}数据/审计meta")))
    assert FS.cmd_plan("2026-10-19") == 6, "增量缺失plan结构化拒绝"


def test_audit_baseline_merges_addendum(tmp_path, monkeypatch):
    """R0原件永不改写; addendum按版本序合并为审计基线(工具版本单独记录)。"""
    monkeypatch.setattr(FS, "FWD_DIR", str(tmp_path))
    monkeypatch.setattr(FS, "FREEZE_P", str(tmp_path / "FREEZE.json"))
    monkeypatch.setattr(FS, "ADMIT_P", str(tmp_path / "ADMIT.json"))
    json.dump({"module_hashes": {"ashare_v2/foo.py": "A" * 64, "ashare_v2/bar.py": "B" * 64},
               "version": "T.R0"}, open(tmp_path / "FREEZE.json", "w", encoding="utf-8"))
    assert FS._audit_baseline() == {"ashare_v2/foo.py": "A" * 64, "ashare_v2/bar.py": "B" * 64}
    json.dump({"module_hashes": {"ashare_v2/bar.py": "C" * 64}},
              open(tmp_path / "FREEZE_ADDENDUM_R0.1.json", "w", encoding="utf-8"))
    ab = FS._audit_baseline()
    assert ab["ashare_v2/foo.py"] == "A" * 64 and ab["ashare_v2/bar.py"] == "C" * 64
    json.dump({"module_hashes": {"ashare_v2/bar.py": "D" * 64}},
              open(tmp_path / "FREEZE_ADDENDUM_R0.2.json", "w", encoding="utf-8"))
    assert FS._audit_baseline()["ashare_v2/bar.py"] == "D" * 64
    assert FS._freeze()["version"] == "T.R0", "R0原件未被覆盖"


def test_chain_head_detects_full_rewrite(tmp_path, monkeypatch):
    """链头独立存档: 整链被重写(重算哈希后自洽)也必须因head.seq/hash不符被verify发现。"""
    codes = ["600030"]
    panel = _synth_panel(codes, end="2026-10-15")
    _fwd_env(tmp_path, monkeypatch, panel)
    assert FS.cmd_plan("2026-10-15") == 0
    rec = FL.read_all(FS.CHAIN_P)[-1]
    rec["nav_est"] = 99999999.0                    # 实质篡改
    open(FS.CHAIN_P, "w", encoding="utf-8").write("")
    FL.append_record(FS.CHAIN_P, rec)             # 重算哈希→链自洽但≠封存头
    assert FS.cmd_verify() == 1, "链头存档不符必须非零退出"
