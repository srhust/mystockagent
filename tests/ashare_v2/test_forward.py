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
    """P0-3: T+1现金约束只许缩减(REDUCE)或撤销(CANCEL), 记录带父plan哈希且单调/换码不可能。"""
    root = str(tmp_path / "led")
    co = PortfolioCoordinator(nav0=C.NAV0, ledger_root=root, ledger_name="rb")
    co.plan_day("2026-06-29", [{"code": "600010", "score_points": 9.0,
                                "engines": ["LONG_CYCLE"], "industry": "I", "record_ids": ["r"]}],
                px={"600010": 10.0}, vol20={"600010": 0.02}, ind_of={"600010": "I"})
    rec = co.plans["2026-06-29"]
    assert rec["buys"], "计划应有买入"
    b = rec["buys"][0]
    orig_hash = b["plan"]["plan_hash"]
    orig_budget = b["cash_budget"]
    assert orig_budget >= 2000.0, f"构造前提: 预算需够演示缩减, got {orig_budget}"
    # ── REDUCE: 现金够最小手但不足原预算 ──
    cut = round(orig_budget * 0.6, 2)
    co.ledger.cash = cut
    co._rebudget_pending_buys(rec, "2026-06-29")
    adj = co.adjust_log[-1]
    assert adj["event"] == "REDUCE" and adj["parent_plan_hash"] == orig_hash
    assert adj["budget_before"] == orig_budget and adj["budget_after"] == cut
    assert adj["monotone_down"] is True and adj["code_unchanged"] is True and adj["code"] == "600010"
    assert b["cash_budget"] == cut and b["plan"]["plan_hash"] == adj["child_plan_hash"]
    # 只减不增: 再喂更多现金也不得回涨
    co.ledger.cash = 99999.0
    co._rebudget_pending_buys(rec, "2026-06-29")
    assert b["cash_budget"] == cut, "预算不得回升"
    assert len([a for a in co.adjust_log if a["code"] == "600010"]) == 1, "无二次调整"
    # ── CANCEL: 现金不足最小申报单位 → 撤销, 不产生零预算假计划 ──
    co.plan_day("2026-06-30", [{"code": "600011", "score_points": 9.0,
                                "engines": ["LONG_CYCLE"], "industry": "J", "record_ids": ["s"]}],
                px={"600011": 20.0}, vol20={"600011": 0.02}, ind_of={"600011": "J"})
    rec2 = co.plans["2026-06-30"]
    b2 = rec2["buys"][0]
    orig2 = b2["cash_budget"]
    co.ledger.cash = 400.0                          # < 20元×100股×1.002
    co._rebudget_pending_buys(rec2, "2026-06-30")
    adj2 = co.adjust_log[-1]
    assert adj2["event"] == "CANCEL" and adj2["budget_after"] == 0.0
    assert adj2["parent_plan_hash"] == b2["plan"]["plan_hash"] and adj2["budget_before"] == orig2
    assert adj2["child_plan_hash"] is None and b2["_cancelled"] is True and b2["cash_budget"] == 0.0
    fills, rejects = co.execute_day("2026-06-30", px_t1={"600011": 20.2}, prev_close={"600011": 20.0})
    assert not [f for f in fills if f["code"] == "600011"], "撤销单不得成交"
    assert any(r.get("reason") == "cancelled_T+1_min_lot" for r in rejects), "撤销须留拒单记录"


def test_p01_forward_plan_execute_uses_raw_open_and_seals(tmp_path, monkeypatch):
    """P0-1/P0-2+纪律: plan→kind标注(E-1时间语义)+DIAGNOSTIC门; execute用增量raw open延迟重建; 幂等拒绝重复。"""
    codes = ["600010", "600011", "600012"]
    panel = _synth_panel(codes)
    _fwd_env(tmp_path, monkeypatch, panel)
    rc = FS.cmd_plan("2026-10-15")
    assert rc == 0
    recs = FL.read_all(FS.CHAIN_P)
    plan_rec = recs[-1]
    assert plan_rec["run_kind"] == "LATE_RECONSTRUCTION", \
        "E-1: 真实钟晚于exec_day 09:25边界的历史信号计划=LATE重建, 非前向样本"
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
    assert isinstance(ex_rec["fills"], list) and isinstance(ex_rec["rejects"], list)
    # 合成缓涨面板无引擎候选(fills空, 结构路径); 真实成交断言由real_panel测试实弹覆盖
    assert FS.cmd_execute("2026-10-16") == 3, "幂等: 重复execute拒绝"


def _fwd_real_env(tmp_path, monkeypatch):
    """真实R31冻结面板环境: 锚2026-10-15 → 真面板10-16信号日=FORWARD(36候选/5买入实弹)。"""
    monkeypatch.setattr(FS, "FWD_DIR", str(tmp_path / "fwd"))
    os.makedirs(tmp_path / "fwd", exist_ok=True)
    monkeypatch.setattr(FS, "CHAIN_P", str(tmp_path / "fwd" / "chain.jsonl"))
    monkeypatch.setattr(FS, "HEAD_P", str(tmp_path / "fwd" / "head.json"))
    monkeypatch.setattr(FS, "STATE_P", str(tmp_path / "fwd" / "state.json"))
    monkeypatch.setattr(FS, "FREEZE_P", str(tmp_path / "fwd" / "FREEZE.json"))
    monkeypatch.setattr(FS, "ADMIT_P", str(tmp_path / "fwd" / "ADMIT.json"))
    monkeypatch.setattr(FI, "ING_DIR", str(tmp_path / "fwd" / "incremental"))
    os.makedirs(FI.ING_DIR, exist_ok=True)
    json.dump({"frozen_trade_date": "2026-10-15", "module_hashes": {}, "version": "T.R0"},
              open(tmp_path / "fwd" / "FREEZE.json", "w", encoding="utf-8"))


def test_p01_real_panel_execute_fills_raw_open(tmp_path, monkeypatch):
    """实弹核心: 真面板plan(2026-10-16)现钟生成=LATE重建标注+DIAGNOSTIC+buys≥4;
    execute(10-19)用增量raw open成交——fills非空且逐笔价格=open(结构性≠close_adj)。"""
    _fwd_real_env(tmp_path, monkeypatch)
    assert FS.cmd_plan("2026-10-16") == 0
    plan_rec = FL.read_all(FS.CHAIN_P)[-1]
    assert plan_rec["run_kind"] == "LATE_RECONSTRUCTION" and plan_rec["formal_gate"] == "DIAGNOSTIC_REHEARSAL", \
        "E-1: 实弹场景plan(10-16)现钟生成已晚于10-19开盘边界→LATE(裁定: 明日补录同样LATE, 不冒充FORWARD)"
    assert len(plan_rec["buys"]) >= 4, f"真面板须产生买入, got {len(plan_rec['buys'])}"
    assert plan_rec["data_source"].startswith("R31 frozen")
    assert FS.cmd_plan("2026-10-16") == 3, "幂等"
    from cycle_system.ashare_v2.data_portal import DataPortal
    frozen = DataPortal(data_mode=C.PIT_RESEARCH).price_panel()
    sig = frozen[frozen["date"] == pd.Timestamp("2026-10-16")].set_index("code")
    codes = [b["code"] for b in plan_rec["buys"]]
    inc = pd.DataFrame({"date": pd.Timestamp("2026-10-19"), "code": codes,
                        "open": [float(sig["close"][c]) * 1.005 for c in codes],
                        "high": [float(sig["close"][c]) * 1.03 for c in codes],
                        "low": [float(sig["close"][c]) for c in codes],
                        "close": [float(sig["close"][c]) * 1.03 for c in codes],
                        "volume": 1e7, "amount": 2e8, "adj_factor": 1.0,
                        "close_adj": [float(sig["close"][c]) * 1.03 for c in codes]})
    monkeypatch.setattr(FI, "load_day", lambda d: inc)
    json.dump({"fetched_at": "2026-10-19T15:30:00+08:00"},
              open(os.path.join(FI.ING_DIR, "2026-10-19.meta.json"), "w", encoding="utf-8"))

    class _AfterCloseDT(datetime):
        @staticmethod
        def now(tz=None):
            return datetime(2026, 10, 20, 9, 0, tzinfo=FS.TZ)   # exec日已收盘次日

    monkeypatch.setattr(FS, "datetime", _AfterCloseDT)
    assert FS.cmd_execute("2026-10-19") == 0
    ex = FL.read_all(FS.CHAIN_P)[-1]
    assert ex["execution_mode"] == "DELAYED_REBUILD_RAW_OPEN"
    assert ex["ledger_target"].startswith("REHEARSAL"), "准入前成交必须进演练账本"
    assert not os.path.exists(os.path.join(FS.FWD_DIR, "ledger", "ledger_dual_cycle_v2_forward.jsonl")), \
        "正式FORWARD绩效账本在准入前必须零写入"
    assert len(ex["fills"]) >= 3, f"实弹成交须非空: {len(ex['fills'])}"
    open_map = inc.set_index("code")["open"].to_dict()
    for f in ex["fills"]:
        assert f["action"] == "BUY" and abs(f["price"] - open_map[f["code"]]) < 1e-9, \
            "成交价必须=当日raw open, 绝不可能是close"
    head = json.load(open(FS.HEAD_P, encoding="utf-8"))
    assert head["ok"] and head["seq"] == 2 and FS.cmd_verify() == 0


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


def test_recovery_plan_crash_state_missing(tmp_path, monkeypatch):
    """指令5失败恢复: PLAN已上链但state崩溃未落 → 禁重复plan(rc7), --recover重建state且
    链记PLAN_RECOVERY(不重复PLAN), 重建的plan哈希与原PLAN逐笔一致。"""
    codes = ["600010", "600011"]
    panel = _synth_panel(codes)
    _fwd_env(tmp_path, monkeypatch, panel)
    assert FS.cmd_plan("2026-10-15") == 0
    rep = FS._state_rehearsal_p()                      # rehearsal域统一: plan/execute同读写此文件
    st = json.load(open(rep, encoding="utf-8"))
    orig_buys = [(b["code"], b["plan"]["plan_hash"]) for b in st["plans"]["2026-10-15"]["buys"]]
    del st["plans"]["2026-10-15"]                     # 模拟append后save_state前崩溃
    json.dump(st, open(rep, "w", encoding="utf-8"))
    assert FS.cmd_plan("2026-10-15") == 7, "崩溃窗口须rc7并给recover指引"
    assert FS.cmd_plan("2026-10-15", recover=True) == 0
    chain = FL.read_all(FS.CHAIN_P)
    assert chain[-1]["event"] == "PLAN_RECOVERY" and chain[-1]["signal_day"] == "2026-10-15"
    assert sum(1 for r in chain if r["event"] == "PLAN") == 1, "不重复PLAN"
    st2 = json.load(open(rep, encoding="utf-8"))
    rebuilt = [(b["code"], b["plan"]["plan_hash"]) for b in st2["plans"]["2026-10-15"]["buys"]]
    assert rebuilt == orig_buys, "恢复重建的冻结计划哈希逐笔一致"


def test_rehearsal_three_day_state_continuity(tmp_path, monkeypatch):
    """审查§6-2(分叉修复验收): 连续三交易日 plan→execute→plan→execute 实弹——
    T+1成交必须进入次日plan初始状态, 正式state/正式账本全程零写入。"""
    _fwd_real_env(tmp_path, monkeypatch)
    from cycle_system.ashare_v2.data_portal import DataPortal
    frozen = DataPortal(data_mode=C.PIT_RESEARCH).price_panel()
    sig = frozen[frozen["date"] == pd.Timestamp("2026-10-16")].set_index("code")
    rep = FS._state_rehearsal_p()

    def _inc(day, mult_o, mult_c):
        codes = list(sig.index)
        return pd.DataFrame({"date": pd.Timestamp(day), "code": codes,
                             "sector": "",                                  # 生产fetch_day同形(空串非NaN)
                             "open": [float(sig["close"][c]) * mult_o for c in codes],
                             "high": [float(sig["close"][c]) * max(mult_o, mult_c) for c in codes],
                             "low": [float(sig["close"][c]) * min(mult_o, mult_c) for c in codes],
                             "close": [float(sig["close"][c]) * mult_c for c in codes],
                             "volume": 1e7, "amount": 2e8, "adj_factor": 1.0,
                             "close_adj": [float(sig["close"][c]) * mult_c for c in codes]})

    inc19, inc20 = _inc("2026-10-19", 1.005, 1.03), _inc("2026-10-20", 1.004, 0.97)
    monkeypatch.setattr(FI, "load_day", lambda d: inc19 if d == "2026-10-19" else inc20)
    for dd, df_ in (("2026-10-19", inc19), ("2026-10-20", inc20)):
        df_.to_csv(os.path.join(FI.ING_DIR, f"{dd}.csv"), index=False)   # 增量层真实落盘(merged只读csv)
        import hashlib as _hl
        sha = _hl.sha256(open(os.path.join(FI.ING_DIR, f"{dd}.csv"), "rb").read()).hexdigest()
        json.dump({"day": dd, "fetched_at": f"{dd}T15:30:00+08:00", "close_trading_after_fetch": True,
                   "source": "akshare(测试注入)", "csv_sha256": sha, "codes_missing": [],
                   "price_basis": "raw+hfq"},
                  open(os.path.join(FI.ING_DIR, f"{dd}.meta.json"), "w", encoding="utf-8"))

    class _DT(datetime):
        @staticmethod
        def now(tz=None):
            return _DT.cur.replace(hour=15, minute=30, tzinfo=FS.TZ)

    _DT.cur = datetime(2026, 10, 19)                  # D1收盘后
    monkeypatch.setattr(FS, "datetime", _DT)
    assert FS.cmd_plan("2026-10-16") == 0             # D1: PLAN(16)
    assert os.path.exists(rep) and not os.path.exists(FS.STATE_P), "D1: state须落rehearsal, 正式零写入"

    _DT.cur = datetime(2026, 10, 20)                  # D2(重建D1计划的成交)
    assert FS.cmd_execute("2026-10-19") == 0          # EXECUTE(16→19)
    st = json.load(open(rep, encoding="utf-8"))
    assert st["cash"] < C.NAV0 and st["ledger_trading_day"] == "2026-10-19", "D2: fills已进rehearsal state"
    assert sum(p["total"] for p in st["pos"].values()) > 0, "D2: 持仓非空(raw open成交入账)"

    assert FS.cmd_plan("2026-10-19") == 0             # D2: PLAN(19) 从成交后的rehearsal state出发
    rec19 = [r for r in FL.read_all(FS.CHAIN_P) if r["event"] == "PLAN" and r["signal_day"] == "2026-10-19"][-1]
    assert rec19["ledger_target"].startswith("REHEARSAL")
    st2 = json.load(open(rep, encoding="utf-8"))
    assert set(st2["plans"]) >= {"2026-10-16", "2026-10-19"}, "次日plan必须看到前日成交后的持仓/现金"

    _DT.cur = datetime(2026, 10, 21)                  # D3: 执行D2计划(可含卖出前日持仓)
    assert FS.cmd_execute("2026-10-20") == 0
    ex20 = FL.read_all(FS.CHAIN_P)[-1]
    assert ex20["event"] == "EXECUTE" and ex20["signal_day"] == "2026-10-19"
    st3 = json.load(open(rep, encoding="utf-8"))
    assert st3["ledger_trading_day"] == "2026-10-20", "演练账本日序连续推进 19→20"
    assert not os.path.exists(FS.STATE_P), "全程: 正式state零写入"
    assert not os.path.exists(os.path.join(FS.FWD_DIR, "ledger", "ledger_dual_cycle_v2_forward.jsonl")), \
        "全程: 正式账本零写入"
    head = json.load(open(FS.HEAD_P, encoding="utf-8"))
    assert head["ok"] and head["seq"] == 4 and FS.cmd_verify() == 0


def test_recovery_execute_blocked_when_ledger_advanced(tmp_path, monkeypatch):
    """失败恢复核心防线(真实R31面板实弹): 真fills已入演练账本但链无EXECUTE → rc7拒绝双记账。"""
    _fwd_real_env(tmp_path, monkeypatch)
    assert FS.cmd_plan("2026-10-16") == 0
    plan_rec = FL.read_all(FS.CHAIN_P)[-1]
    assert plan_rec["buys"], "实弹前提: 真面板产生买入计划"
    from cycle_system.ashare_v2.data_portal import DataPortal
    frozen = DataPortal(data_mode=C.PIT_RESEARCH).price_panel()
    sig = frozen[frozen["date"] == pd.Timestamp("2026-10-16")].set_index("code")
    co = PortfolioCoordinator.load_state(FS._state_rehearsal_p(), nav0=C.NAV0,
                                         ledger_root=os.path.join(FS.FWD_DIR, "ledger"),
                                         ledger_name="dual_cycle_v2_fwd_rehearsal")
    fills, _ = co.execute_day("2026-10-16",
                              px_t1={c: float(v) * 1.005 for c, v in sig["close"].items()},
                              prev_close={c: float(v) for c, v in sig["close"].items()})
    co.save_state(FS._state_rehearsal_p())
    assert len(fills) >= 3, f"实弹前提: 真实fills入账(演练账本jsonl已落盘), got {len(fills)}"
    json.dump({"fetched_at": "2026-10-19T15:30:00+08:00"},
              open(os.path.join(FI.ING_DIR, "2026-10-19.meta.json"), "w", encoding="utf-8"))
    monkeypatch.setattr(FI, "load_day", lambda d: pd.DataFrame({"code": [], "open": []}))
    assert FS.cmd_execute("2026-10-19") == 7, "账本已推进且链无记录: 必须拒绝自动重放(人工对账)"


def test_recovery_plan_crash_state_missing_real_panel(tmp_path, monkeypatch):
    """失败恢复(实弹): 真面板5买入的PLAN上链后state崩溃 → rc7指引 → --recover重建,
    全部冻结plan哈希逐笔一致, 链不重复PLAN。"""
    _fwd_real_env(tmp_path, monkeypatch)
    assert FS.cmd_plan("2026-10-16") == 0
    rep = FS._state_rehearsal_p()                      # rehearsal域统一(plan save到rehearsal)
    st = json.load(open(rep, encoding="utf-8"))
    orig_buys = [(b["code"], b["plan"]["plan_hash"], b["plan"]["cash_budget"])
                 for b in st["plans"]["2026-10-16"]["buys"]]
    assert len(orig_buys) >= 4, "实弹前提: 真面板有买入计划"
    del st["plans"]["2026-10-16"]
    json.dump(st, open(rep, "w", encoding="utf-8"))
    assert FS.cmd_plan("2026-10-16") == 7
    assert FS.cmd_plan("2026-10-16", recover=True) == 0
    chain = FL.read_all(FS.CHAIN_P)
    assert chain[-1]["event"] == "PLAN_RECOVERY"
    assert sum(1 for r in chain if r["event"] == "PLAN") == 1
    st2 = json.load(open(rep, encoding="utf-8"))
    rebuilt = [(b["code"], b["plan"]["plan_hash"], b["plan"]["cash_budget"])
               for b in st2["plans"]["2026-10-16"]["buys"]]
    assert rebuilt == orig_buys, "恢复重建的冻结计划(含预算)逐笔哈希一致"
    assert not os.path.exists(FS.STATE_P), "rehearsal模式: 正式state全程零写入"


def test_ingest_fetch_pipeline_synthetic(tmp_path, monkeypatch):
    """P0-2正路径全链: fetch_day落盘→meta/sha审计→load_day验证→require_plan_ready→merge。
    akshare接口用monkeypatch假数据(真连通已实测: fetch_one双口径至2026-10-16)。"""
    import ashare_stock_pipeline as real_pipe
    monkeypatch.setattr(FI, "ING_DIR", str(tmp_path / "inc"))
    day = "2026-10-19"
    fake_raw = pd.DataFrame([{"date": day, "open": 10.0, "high": 10.5, "low": 9.9,
                              "close": 10.2, "volume": 1e7, "amount": 2e8}])
    fake_hfq = pd.DataFrame([{"date": day, "close": 20.4}])          # factor=2.0

    def fake_fetch_one(code, start="20200101", adjust=""):
        return fake_raw if adjust == "" else fake_hfq
    monkeypatch.setattr(real_pipe, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(real_pipe, "configured_stocks", lambda: ["600010", "600011"])
    monkeypatch.setattr(FI, "datetime_now_iso", lambda: f"{day}T15:30:00+08:00")
    meta = FI.fetch_day(day, per_call_sleep=0.0)
    assert meta["rows"] == 2 and meta["close_trading_after_fetch"] is True
    assert meta["codes_missing"] == [] and len(meta["csv_sha256"]) == 64
    df = FI.load_day(day)                                           # sha256逐字节复验
    assert float(df["adj_factor"].iloc[0]) == 2.0 and float(df["close_adj"].iloc[0]) == 20.4
    assert FI.require_plan_ready(day)["fetched_at"].endswith("+08:00")
    # 篡改审计: csv被改写→load_day必须raise
    open(os.path.join(FI.ING_DIR, f"{day}.csv"), "a", encoding="utf-8").write("junk\n")
    with pytest.raises(ValueError, match="哈希失配"):
        FI.load_day(day)
    # 幂等重抓: 复用既有meta不重写(先还原csv使哈希自洽)
    pd.DataFrame([{"date": day, "code": "600010", "sector": "", "open": 10.0, "high": 10.5,
                   "low": 9.9, "close": 10.2, "volume": 1e7, "amount": 2e8,
                   "adj_factor": 2.0, "close_adj": 20.4},
                  {"date": day, "code": "600011", "sector": "", "open": 10.0, "high": 10.5,
                   "low": 9.9, "close": 10.2, "volume": 1e7, "amount": 2e8,
                   "adj_factor": 2.0, "close_adj": 20.4}]).to_csv(
        os.path.join(FI.ING_DIR, f"{day}.csv"), index=False, encoding="utf-8")
    meta["csv_sha256"] = FI._sha256(os.path.join(FI.ING_DIR, f"{day}.csv"))
    json.dump(meta, open(os.path.join(FI.ING_DIR, f"{day}.meta.json"), "w", encoding="utf-8"))
    calls = []
    monkeypatch.setattr(real_pipe, "fetch_one", lambda *a, **k: (calls.append(1), fake_raw)[1])
    meta2 = FI.fetch_day(day, per_call_sleep=0.0)
    assert meta2["csv_sha256"] == meta["csv_sha256"] and not calls, "幂等: 已抓取日不重抓"
    # merge: 增量日不得覆盖冻结日期
    frozen = _synth_panel(["600010"], end="2026-10-16")
    merged = FI.merge_with_frozen(frozen, [day])
    assert len(merged[merged["date"] == pd.Timestamp(day)]) == 2
    with pytest.raises(ValueError, match="冲突"):
        d16 = df.copy()
        d16["date"] = pd.Timestamp("2026-10-16")
        d16.to_csv(os.path.join(FI.ING_DIR, "2026-10-16.csv"), index=False, encoding="utf-8")
        m16 = dict(meta); m16["day"] = "2026-10-16"
        m16["csv_sha256"] = FI._sha256(os.path.join(FI.ING_DIR, "2026-10-16.csv"))
        json.dump(m16, open(os.path.join(FI.ING_DIR, "2026-10-16.meta.json"), "w", encoding="utf-8"))
        FI.merge_with_frozen(frozen, ["2026-10-16"])
    with pytest.raises(FileNotFoundError, match="先 fetch"):
        FI.merge_with_frozen(frozen, ["2026-10-20"])       # 未抓取日 fail-closed
    # 内容日期与文件名不符: 审计拒绝(本测试由此洞被负例逼出后补的fail-closed)
    d15 = df.copy()
    d15["date"] = pd.Timestamp("2026-10-15")
    d15.to_csv(os.path.join(FI.ING_DIR, "2026-10-21.csv"), index=False, encoding="utf-8")
    m21 = dict(meta); m21["csv_sha256"] = FI._sha256(os.path.join(FI.ING_DIR, "2026-10-21.csv"))
    json.dump(m21, open(os.path.join(FI.ING_DIR, "2026-10-21.meta.json"), "w", encoding="utf-8"))
    with pytest.raises(ValueError, match="内容日期与文件名不符"):
        FI.load_day("2026-10-21")


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


def test_e1_forward_time_boundary(tmp_path, monkeypatch):
    """裁定E-1/E-3: FORWARD须证明计划在exec_day 09:25信息边界前生成;
    晚生成的历史信号=LATE_RECONSTRUCTION, 永不入正式账本(禁回填提升FORWARD计数)。"""
    codes = ["600010", "600011", "600012"]
    panel = _synth_panel(codes)
    _fwd_env(tmp_path, monkeypatch, panel)
    # 1) 三值边界纯函数: 锚10-14, 信号10-15→exec 10-16
    assert FS._kind_refined("2026-10-15", "2026-10-16", "2026-10-15T18:00:00+08:00") == "FORWARD"
    assert FS._kind_refined("2026-10-15", "2026-10-16", "2026-10-16T09:24:59+08:00") == "FORWARD"
    assert FS._kind_refined("2026-10-15", "2026-10-16", "2026-10-16T09:25:00+08:00") == "LATE_RECONSTRUCTION"
    assert FS._kind_refined("2026-10-13", "2026-10-14", "2026-10-13T18:00:00+08:00") == "REPLAY"
    # 2) FORWARD集成: fake钟=信号日18:00, 早于执行日开盘边界 → 链记录FORWARD+kind_basis
    class _PreOpenDT(datetime):
        @staticmethod
        def now(tz=None):
            return datetime(2026, 10, 15, 18, 0, tzinfo=FS.TZ)
    monkeypatch.setattr(FS, "datetime", _PreOpenDT)
    assert FS.cmd_plan("2026-10-15") == 0
    rec = FL.read_all(FS.CHAIN_P)[-1]
    assert rec["run_kind"] == "FORWARD" and rec["kind_basis"].startswith("E-1")
    # 3) LATE+FORMAL拒绝: 新环境+真实钟(现>10-16 09:25边界)+ADMIT存在+formal → rc=8零副作用
    monkeypatch.setattr(FS, "datetime", datetime)
    _fwd_env(tmp_path / "e2", monkeypatch, panel)
    json.dump({"note": "admitted"}, open(FS.ADMIT_P, "w", encoding="utf-8"))
    assert FS.cmd_plan("2026-10-15", formal=True) == 8, "LATE重建永不接受FORMAL账本"
    assert not os.path.exists(FS.CHAIN_P), "拒绝须零副作用(无半写记录)"

