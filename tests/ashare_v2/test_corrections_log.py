# -*- coding: utf-8 -*-
"""test_corrections_log — 包E: 更正追加账机制(不改写/幂等/hash锚)。"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "..", "..", "cycle_system", "ashare_v2", "tools"))
import pytest                                        # noqa: E402
from corrections_log import LOG_P, register, sha256_text  # noqa: E402


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr("corrections_log.LOG_P", str(tmp_path / "COR.jsonl"))


def test_append_only_ordering(tmp_log):
    import corrections_log
    r1 = register("doc#a", "原句甲", "更正甲", "裁定1", ts="T1")
    r2 = register("doc#b", "原句乙", "更正乙", "裁定2", ts="T2")
    lines = [json.loads(l) for l in open(corrections_log.LOG_P, encoding="utf-8")]
    assert [l["id"] for l in lines] == [1, 2]
    assert r1["op"] == r2["op"] == "APPEND_ONLY"


def test_duplicate_idempotent_reject(tmp_log):
    import corrections_log
    register("doc#a", "同一原句", "同一更正", "裁定1")
    with pytest.raises(RuntimeError, match="DUPLICATE_CORRECTION"):
        register("doc#a-改定位", "同一原句", "同一更正", "裁定1(重登)")
    # 同original不同corrected_to = 允许的二次更正(追加)
    register("doc#a", "同一原句", "更准确的更正", "裁定2")


def test_sha_anchors_original_text(tmp_log):
    import corrections_log
    txt = "价格口径修正贡献+7.10pp"
    r = register("ref", txt, "PATHWISE框架", "裁定A")
    assert r["original_sha256"] == sha256_text(txt)
    # 原文本身从未被写入/改写(append-only账本, 无original文件touch)
    assert not os.path.exists("ref")
