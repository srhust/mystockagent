# -*- coding: utf-8 -*-
"""生成/更新 output/ashare_v2/experiment_manifest.json (§10: 全部结果的追溯总账)。

可重复执行: 每阶段结束跑一次, 记录该阶段产物清单+哈希+代码版本+配置哈希+数据锚+显式标注。
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
from cycle_system.ashare_v2 import config as C  # noqa: E402

OUT = os.path.join(C.OUTPUT_DIR, "experiment_manifest.json")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def git(*a):
    return subprocess.run(["git", "-C", ROOT] + list(a), capture_output=True, text=True).stdout.strip()


def main():
    tz = timezone(timedelta(hours=8))
    inv = {}
    for dirpath, _, files in os.walk(C.OUTPUT_DIR):
        for f in sorted(files):
            if f == "experiment_manifest.json":
                continue
            p = os.path.join(dirpath, f)
            inv[os.path.relpath(p, ROOT).replace("\\", "/")] = {"sha256": sha(p), "size": os.path.getsize(p)}
    m = {
        "project": "Ashare Dual-Cycle V2",
        "task_spec": "cycle_system/ashare_v2/TASK_SPEC.md",
        "branch": git("branch", "--show-current"),
        "code_version": git("rev-parse", "HEAD"),
        "worktree_dirty": bool(git("status", "--porcelain")),
        "config_hash": C.config_hash(),
        "data_anchors": {
            "stocks_manifest_sha256": sha(os.path.join(ROOT, "output", "ashare", "manifest.json")),
            "n_csv": 250, "csv_vs_manifest": "250/250 PASS (baseline_integrity C1)",
            "frozen_code_hashes": "C7: PASS_ANNOTATED(swing_m1 R35/R36只读演进, F2重放逐字段一致) 其余PASS",
        },
        "f2_baseline": {"status": "BASELINE_REPRODUCED", "ref": "output/ashare_v2/f2_repro/f2_repro_resolution.json"},
        "regression": {"existing_suite": "48 passed (pytest tests -q, 2026-10-18 21:30)",
                       "note": "data_audit.* 时间戳为测试副作用, 已复位并标注",
                       "ref": "output/ashare_v2/regression/"},
        "explicit_annotations": [
            "SURVIVORSHIP_BIAS_PARTIAL: 当前249/250池未消除幸存者偏差, 正式回测资格受限",
            "PIT: 申万二级历史成员=UNKNOWN; sector列=CURRENT_SNAPSHOT_ONLY; Strict模式行业级正式信号DATA_BLOCKED(阶段B落地)",
            "ST_FLAG_UNVERIFIED: 历史ST不可得; suspension以缺行近似",
            "CALIBRATED=False: 评分未校准, 禁称预期收益率/胜率",
            "RESEARCH_WINDOW: 2020-2026非OOS; 未经独立样本外验证不声明预测有效",
            "core.autocrlf=true无.gitattributes: 磁盘哈希行尾脆弱, 完整性用git-blob+CRLF双口径",
        ],
        "phases": {"A": "完成", "B": "完成", "C": "完成", "D": "完成", "E": "待做", "F": "待做", "G": "待做"},
        "artifacts": inv,
        "generated_at": datetime.now(tz).isoformat(),
    }
    json.dump(m, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(OUT, "artifacts:", len(inv))


if __name__ == "__main__":
    main()
