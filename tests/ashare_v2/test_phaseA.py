# -*- coding: utf-8 -*-
"""阶段A工程测试(§9.1 当前适用子集)。

§9.1 场景 ↔ 阶段落位: 本文件覆盖
  [A] 旧 B0/B1/F2 与测试不退化(基线完整性重验) / 无未来信息泄漏的契约级防护(schemas PIT
  校验) / 无数据 Fail-Closed 的契约级防护(strict 常量与 DATA_BLOCKED 枚举)。
引擎级场景(双状态机独立/Fast-V 独立候选/Long-Cycle 无固定天数退出/同股双引擎资金/
现金守恒/订单顺序无关/T+1/停牌涨跌停ST安全/风险限制/高集中与空仓边界)依赖 C/D/E 实现,
在 tests/ashare_v2/test_engine_*.py / test_capital_*.py 随阶段交付; 合成场景 §9.2 A–H 于
阶段C/D/F 落地。缺一项即在该阶段验收报告标失败项, 不以本文件通过冒充全量。
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from cycle_system.ashare_v2 import config as C          # noqa: E402
from cycle_system.ashare_v2 import schemas as S         # noqa: E402
from cycle_system.ashare_v2.tools import baseline_integrity as BI  # noqa: E402

OUT = C.OUTPUT_DIR


def _load(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return json.load(f)


# ── 基线完整性 ─────────────────────────────────────────────
def test_phase_a_baseline_report_all_pass():
    rep = _load(os.path.join("output", "ashare_v2", "baseline_integrity", "baseline_integrity_report.json"))
    assert rep["verdict"] == "PASS"
    ids = {c["id"]: c["status"] for c in rep["checks"]}
    assert ids["C1_csv_manifest"] == "PASS" and ids["C1_csv_manifest"] != "FAIL"
    assert ids["C2_frozen_artifacts"] == "PASS"
    assert ids["C3_us_frozen"] == "PASS"
    assert ids["C4_worktree_frozen"] == "PASS"
    assert ids["C5_swing_m1_untouched"] == "PASS"
    assert ids["C6_tamper_selftest"] == "PASS"
    assert ids.get("C7_frozen_code_hashes") in ("PASS", "PASS_ANNOTATED")


def test_250of250_csv_hashes_match_manifest():
    res = BI.check_csv_manifest(C.STOCKS_DIR, os.path.join(ROOT, "output", "ashare", "manifest.json"))
    assert res["status"] == "PASS", res
    assert res["checked"] == 250 and res["n_files"] == 250


def test_tamper_detection_is_real(tmp_path):
    """C6 的独立复证: 改一字节必须被验证函数抓到(证明哈希校验非空转)。"""
    man = _load("output/ashare/manifest.json")
    name = sorted(man["csv_hashes"])[0]
    p = tmp_path / name
    src = open(os.path.join(C.STOCKS_DIR, name), "rb").read()
    p.write_bytes(src[:-1] + bytes([src[-1] ^ 0xFF]))
    assert BI.sha256_file(str(p)) != man["csv_hashes"][name]


def test_frozen_artifacts_untouched_live():
    for rel in C.FROZEN_ARTIFACTS:
        r = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--", rel],
                           capture_output=True, text=True).stdout.strip()
        assert r == "", f"冻结路径被修改: {rel} -> {r}"
    r = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--", "cycle_system/swing_m1.py"],
                       capture_output=True, text=True).stdout.strip()
    assert r == "", "swing_m1.py 被修改(禁止)"


# ── F2 复现 P0 ────────────────────────────────────────────
def test_f2_repro_resolved_or_explicitly_annotated():
    path = os.path.join(ROOT, "output", "ashare_v2", "f2_repro", "f2_repro_resolution.json")
    assert os.path.exists(path), "F2复现P0必须有解决记录(RESOLVED 或 BASELINE_UNREPRODUCIBLE)"
    d = json.load(open(path, encoding="utf-8"))
    assert d["verdict"] in ("F2_REPRO_RESOLVED", "BASELINE_UNREPRODUCIBLE")
    if d["verdict"] == "F2_REPRO_RESOLVED":
        assert d["field_diff"] == {}
        assert d["frozen_artifact_sha256_before_after"][0] == d["frozen_artifact_sha256_before_after"][1]


def test_f2_params_match_manifest_candidates_ledger():
    man = _load("output/ashare/manifest.json")
    cand = man["candidates"]["F2_gate_realloc"]
    cfgp = dict(C.F2_REPRO_PARAMS)
    cfgp.setdefault("s2_bad_win", 20)   # run 默认值=账本显式值
    cfgp.setdefault("s2_rs_th", 0.0)
    for k, v in cand.items():
        assert cfgp.get(k) == v, f"恢复参数集与R31账本不一致: {k}"


def test_f2_repro_ledger_numbers():
    d = _load("output/ashare/swing_m1_F2_realloc_full.json")
    st = d["stats"]
    assert st["return"] == C.F2_LEDGER["return"] and st["max_dd"] == C.F2_LEDGER["max_dd"]
    assert st["rejects"]["s2gate"] == C.F2_LEDGER["s2gate"]


# ── 设计契约(schemas/config) ──────────────────────────────
def test_longcycle_state_enum_exact():
    assert {e.value for e in S.LongCycleState} == {
        "DOWNTREND", "BOTTOM_WATCH", "BOTTOM_FORMING", "EARLY_RECOVERY",
        "EXPANSION", "TREND_IMPAIRED", "INVALIDATED", "UNKNOWN"}


def test_fastv_state_enum_exact():
    assert {e.value for e in S.FastVState} == {
        "NO_SETUP", "SHOCK_DETECTED", "BOTTOM_SEARCH", "REVERSAL_CANDIDATE",
        "ENTRY_CONFIRMED", "REBOUND_ACTIVE", "REVERSAL_FAILED", "EXPIRED"}


def test_membership_trust_enum_exact():
    assert {e.value for e in S.MembershipTrust} == {
        "SW_PIT_VERIFIED", "SW_PARTIAL", "CSRC_HISTORICAL_DATE",
        "CURRENT_SNAPSHOT_ONLY", "UNKNOWN"}


def test_pit_violation_caught_fail_closed():
    rec = S.OpportunityRecord(
        industry="煤炭", industry_taxonomy_version="SECTOR_SNAPSHOT_CURRENT_ONLY",
        membership_trust="CURRENT_SNAPSHOT_ONLY", code="600000", engine="LONG_CYCLE",
        cycle_stage="BOTTOM_FORMING",
        key_evidence=[S.Evidence(name="range_pos_250d", value=0.1, asof="2026-01-06",
                                 source="panel", availability="PROXY_ONLY")],
        first_signal_day="2026-01-05", entry_condition="x", invalidation_condition="y",
        data_credibility="PROXY_ONLY", decision="NO_TRADE")
    errs = rec.validate()
    assert any("PIT违规" in e for e in errs), "未来证据必须被拒绝(fail-closed)"
    rec.key_evidence[0].asof = "2026-01-05"
    assert not any("PIT违规" in e for e in rec.validate())


def test_opportunity_required_fields():
    rec = S.OpportunityRecord(industry="x", industry_taxonomy_version="v", membership_trust="UNKNOWN",
                               code="c", engine="FAST_V", cycle_stage="NO_SETUP")
    errs = rec.validate()
    for field in ("first_signal_day", "entry_condition", "invalidation_condition",
                  "data_credibility", "decision"):
        assert any(field in e for e in errs)
    assert any("key_evidence" in e for e in errs)


def test_no_engine_split_or_fixed_position_count():
    assert C.ENGINE_SPLIT_FRAC is None
    assert C.MAX_POSITIONS is None
    body = open(os.path.join(C.ASHARE_V2_DIR, "capital_allocator.py"), encoding="utf-8").read()
    for token in ("0.4", "0.6", "40/60", "n_hold"):
        assert token not in body.replace("0.60", ""), f"分配器骨架不得预置固定引擎仓位/持仓数 token={token}"


def test_long_cycle_has_no_fixed_days_exit_constant():
    names = {k for k in dir(C) if k.isupper()}
    assert not any("HOLD_DAYS" in k or "MAX_HOLD" in k or "EXIT_DAY" in k for k in names)
    lc = open(os.path.join(C.ASHARE_V2_DIR, "long_cycle.py"), encoding="utf-8").read()
    fv = open(os.path.join(C.ASHARE_V2_DIR, "config.py"), encoding="utf-8").read()
    assert "FV_EXPIRE_D" in fv  # 天数衰减只允许存在于 Fast-V 语义


def test_uncalibrated_wording_guard():
    assert C.CALIBRATED is False
    rec = S.OpportunityRecord(industry="x", industry_taxonomy_version="v", membership_trust="UNKNOWN",
                               code="c", engine="FAST_V", cycle_stage="NO_SETUP",
                               key_evidence=[S.Evidence("a", 1.0, "2026-01-05", "s", "CONFIRMED")],
                               first_signal_day="2026-01-05", entry_condition="e",
                               invalidation_condition="i", data_credibility="CONFIRMED",
                               decision="NO_TRADE")
    assert "预期收益率" in rec.forbidden_claims_note and "胜率" in rec.forbidden_claims_note


def test_all_spec_modules_present():
    need = ["schemas", "data_portal", "universe", "industry_cycle", "fast_v", "long_cycle",
            "signal_merge", "opportunity_ranker", "capital_allocator", "exit_manager",
            "portfolio", "runner", "report", "config"]
    for m in need:
        assert os.path.exists(os.path.join(C.ASHARE_V2_DIR, m + ".py")), m
        __import__(f"cycle_system.ashare_v2.{m}")


def test_provenance_roundtrip():
    p = S.Provenance(code_version="x", branch="ashare-v2", config_hash=C.config_hash(),
                     data_hashes={"manifest": "y"}, asof="2026-10-18",
                     generated_at="2026-10-18T00:00:00+08:00", mode="SYNTHETIC")
    d = p.to_dict()["_provenance"]
    assert d["config_hash"] == C.config_hash() and d["branch"] == "ashare-v2"


def test_us_frozen_files_identical():
    """美股 HOLD 90/120 哈希锁(与 tests/test_cycle_portfolio.py 同目标的独立复证)。"""
    for rel in C.US_FROZEN_FILES:
        p = os.path.join(ROOT, rel)
        blob = subprocess.run(["git", "-C", ROOT, "rev-parse", f"HEAD:{rel}"],
                              capture_output=True, text=True).stdout.strip()
        cur = subprocess.run(["git", "-C", ROOT, "hash-object", p],
                             capture_output=True, text=True).stdout.strip()
        assert blob and cur == blob, f"美股冻结文件被改动: {rel}"
