# -*- coding: utf-8 -*-
"""独立验收第5项测试: 严格模式四阻断条件契约 + 研究模式显式降级。"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from cycle_system.ashare_v2 import config as C                                # noqa: E402
from cycle_system.ashare_v2 import strict_gate as SG                          # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                     # noqa: E402


def test_four_gates_contract_defined():
    ids = [g["id"] for g in SG.GATES]
    assert ids == ["G1_SW_PARTIAL", "G2_MARKET_WIDE_POOL", "G3_ST_FLAG", "G4_FUNDAMENTAL_TS"]
    for g in SG.GATES:
        assert g["strict_effect"].startswith(("BLOCK", "REJECT", "DATA_BLOCKED")), "strict必须阻断语义"
        assert "DEGRADE" in g["research_effect"], "研究模式必须显式降级而非伪装通过"
        assert g["evidence"], "每条件必须指向实装点"


def test_strict_blocks_and_research_degrades_realdata():
    """真实数据运行时探针(与tools/strict_gate_report.py同一函数的轻量版): G2/G4/降级标注。"""
    portal = DataPortal(data_mode=C.PIT_RESEARCH)
    rep = SG.evaluate(portal=portal)
    assert rep["probe"]["market_wide_status"] == "DATA_BLOCKED"
    assert rep["probe"]["sw_dataset_status"] == "PIT_UNVERIFIED"
    assert rep["probe"]["fundamental_blocked"] == rep["probe"]["fundamental_total"] > 0
    assert any("ST_FLAG_UNVERIFIED" in a for a in rep["probe"]["annotations"])
    assert any("SURVIVORSHIP" in a for a in rep["probe"]["annotations"])
    assert rep["strict_formal_signals_possible"] is False
    # G4: 指标目录全部 DATA_BLOCKED → strict 拒绝路径在 data_portal 已锁(test_phaseB), 这里锁折价契约
    from cycle_system.ashare_v2 import opportunity_ranker as R
    assert R._CRED["DATA_BLOCKED"] == 0.10 and R._CRED["PROXY_ONLY"] == 0.35
    asof = SG.as_of_data_mode(C.PIT_RESEARCH)
    assert asof["strict_blocks"] is False and asof["degrade_required"] is True
    asof2 = SG.as_of_data_mode(C.PIT_STRICT)
    assert asof2["strict_blocks"] is True and asof2["degrade_required"] is False


def test_strict_gate_artifact_if_present():
    p = os.path.join(C.OUTPUT_DIR, "independent", "strict_gate.json")
    if not os.path.exists(p):
        pytest.skip("由tools/strict_gate_report.py生成后校验")
    d = json.load(open(p, encoding="utf-8"))
    assert d["four_gates_confirmed"] is True
    assert d["probe"]["strict_candidates_n"] == 0, "STRICT下行业正式信号必须为0"
    assert len(d["gates"]) == 4
    assert d["config_hash"] == C.config_hash(), "契约随config变更失效(篡改检测)"
