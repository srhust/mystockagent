# -*- coding: utf-8 -*-
"""阶段F工程测试: 三模式边界(SYNTHETIC全流程守恒/十产物齐全/DIAGNOSTIC零账本) + 合成A-H
机制覆盖 (§9.1资金守恒/T+1/涨停 + §9.2 E-H 在协调器路径复现)。"""
import json
import os
import sys

import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from cycle_system.ashare_v2 import config as C                                    # noqa: E402
from cycle_system.ashare_v2 import report as RP                                   # noqa: E402
from cycle_system.ashare_v2.runner import Runner                                  # noqa: E402
from cycle_system.ashare_v2.schemas import RunMode                                # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "cycle_system", "ashare_v2", "tools"))
import phaseF_run as PF                                                           # noqa: E402

TEN = ["state_engine.json", "opportunities.csv", "trades.csv", "nav_curve.csv", "attribution.csv",
       "allocation_plan_log.csv", "data_quality_report.json", "industry_stage_snapshot.json",
       "universe_buckets_sample.json", "findings.md"]


@pytest.fixture(scope="module")
def synth_runner_result(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("synth_led"))
    r = Runner(RunMode.SYNTHETIC.value, portal=PF.SynthPortal(PF.synth_panel()),
               window=("2024-06-03", "2025-06-30"), ledger_root=root, ledger_name="tsynth")
    res = r.run()
    return r, res, root


def test_synthetic_conservation_and_reconcile(synth_runner_result):
    r, res, root = synth_runner_result
    assert res["conservation_ok"], "nav=cash+Σ持仓 守恒"
    assert res["virtual_real_reconciled"], "虚拟头寸合计=真实持仓(资金单承诺)"
    assert res["days_simulated"] > 150
    assert os.path.isfile(os.path.join(root, "ledger_tsynth.jsonl")), "影子账本审计文件真实落盘"
    for n in res["nav_hist"]:
        assert n["cash"] >= -1e-9, "现金不得为负(T+1与费用守恒)"


def test_synthetic_ten_artifacts(synth_runner_result):
    r, res, root = synth_runner_result
    outdir = os.path.join(root, "artifacts")
    files = RP.write_all(outdir, r, res)
    assert files == TEN, "§10十产物清单"
    for fn in files:
        p = os.path.join(outdir, fn)
        assert os.path.isfile(p) and os.path.getsize(p) > 0
    txt = open(os.path.join(outdir, "findings.md"), encoding="utf-8").read()
    assert "NOT_CLAIMED" in txt and "四态分离" in txt
    assert "预测有效: NOT_CLAIMED" in txt
    for jsonf in ("state_engine.json", "data_quality_report.json", "industry_stage_snapshot.json",
                  "universe_buckets_sample.json"):
        prov = json.load(open(os.path.join(outdir, jsonf), encoding="utf-8"))["_provenance"]
        assert prov["config_hash"] == C.config_hash() and prov["mode"] == RunMode.SYNTHETIC.value


def test_synthetic_engine_activity(synth_runner_result):
    r, res, root = synth_runner_result
    buys = sum(p["n_buys"] for p in res["plan_log"])
    sells = sum(p["n_sells"] for p in res["plan_log"])
    assert res["n_fills"] > 0 and buys > 0, "合成面板必须产生真实买卖流转(E场景)"
    assert len(r.opportunities) > 0
    cands_engines = {tuple(m["merged"]["engines"]) for m in r.opportunities}
    assert ("LONG_CYCLE",) in cands_engines or {"LONG_CYCLE", "FAST_V"} in cands_engines
    assert any("FAST_V" in c for c in cands_engines), "FV引擎候选亦进入流转"


def test_opportunities_csv_no_future_leak(synth_runner_result):
    r, res, root = synth_runner_result
    days = {o["day"] for o in r.opportunities}
    assert all(d <= res["window"][1] for d in days), "记录日≤窗口末日(前缀因果)"


def test_diagnostic_zero_ledger(tmp_path):
    r = Runner(RunMode.DIAGNOSTIC.value, portal=PF.SynthPortal(PF.synth_panel()),
               window=("2025-01-06", "2025-03-31"))
    r.setup()
    assert r.coord is None, "DIAGNOSTIC不构造协调器/账本"
    with pytest.raises(RuntimeError):
        r.run()
    last = str(pd.Timestamp(r.dates[-1]).date())
    diag = r.diagnose(last)
    assert diag["candidates_merged"] >= 0
    assert diag["hypothetical_allocation"]["cash_conservation"]["ok"]


def test_mode_validation():
    with pytest.raises(ValueError):
        Runner("LIVE")
    with pytest.raises(ValueError):
        Runner("PRODUCTION")


def test_shadow_rejects_are_explicit(synth_runner_result):
    # 合成面板内出现执行拒绝时, 必须落入rejects而非静默(用同fixture的日志检查结构)
    r, res, root = synth_runner_result
    for x in r.coord.execution_log:
        for j in x["rejects"]:
            assert j["reason"] in {"halted_or_no_price", "limit_up", "limit_down", "insufficient_cash_after_fees",
                                   "bad_lot", "invalid_price", "sellable_violation"}, j["reason"]
