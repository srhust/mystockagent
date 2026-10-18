# -*- coding: utf-8 -*-
"""阶段A工具: 冻结基线完整性验证器(只读, 除 output/ashare_v2 外不写任何文件)。

检查项:
  C1 250/250 日线CSV sha256 ↔ output/ashare/manifest.json (R31数据锚)
  C2 冻结产物 git-blob 一致性(B0/B1/F2/manifest/f2_repro_p0/data_audit/flow_audit/dd_attr_v2)
     + F2 冻结账本数字 ↔ config.F2_LEDGER
  C3 美股 HOLD 90/120 冻结文件存在+当前哈希记录(tests/test_cycle_portfolio.py 为权威哈希锁)
  C4 工作区对冻结路径零修改(git status porcelain)
  C5 swing_m1.py 与 HEAD 一致(run() 语义未动)
  C6 篡改自检: 内存复制一份CSV改字节→验证器必须报FAIL(证明C1不是空转)
  C7 R31冻结代码哈希: manifest.code_hashes(swing_m1/executor/tradeable_pool/stock_pipeline)
输出: output/ashare_v2/baseline_integrity/baseline_integrity_report.{json,md} (含溯源头)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))  # 仓库根(勿插ashare_v2目录: 会遮蔽根config)
from cycle_system.ashare_v2 import config as C  # noqa: E402

ROOT = C.REPO_ROOT


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True).stdout.strip()


def check_csv_manifest(csv_dir: str, manifest_path: str) -> dict:
    man = json.load(open(manifest_path, encoding="utf-8"))
    hashes = man["csv_hashes"]
    files = {f for f in os.listdir(csv_dir) if f.endswith(".csv")}
    missing, extra, mismatched = [], [], []
    for name, want in sorted(hashes.items()):
        p = os.path.join(csv_dir, name)
        if not os.path.exists(p):
            missing.append(name)
        elif sha256_file(p) != want:
            mismatched.append(name)
    for f in sorted(files):
        if f not in hashes:
            extra.append(f)
    ok = (not missing) and (not mismatched) and (not extra) and len(hashes) == man.get("n_csv", len(hashes)) == len(files)
    return {"status": "PASS" if ok else "FAIL",
            "n_manifest": man.get("n_csv"), "n_files": len(files), "checked": len(hashes),
            "missing": missing[:10], "extra": extra[:10], "mismatched": mismatched[:10],
            "manifest_generated": man.get("generated"), "manifest_git_sha": man.get("git_sha"),
            "manifest_sha256": sha256_file(manifest_path)}


def check_tamper_selftest(csv_hashes: dict, csv_dir: str) -> dict:
    """C6: 复制一份CSV到临时目录并翻转一字节, 期望验证逻辑判FAIL。"""
    name = sorted(csv_hashes)[0]
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(csv_dir, name)
        dst = os.path.join(td, name)
        with open(src, "rb") as f:
            data = bytearray(f.read())
        data[-1] ^= 0xFF
        with open(dst, "wb") as f:
            f.write(bytes(data))
        detected = sha256_file(dst) != csv_hashes[name]
    return {"status": "PASS" if detected else "FAIL", "detail": f"翻转 {name} 末字节, 哈希失配应检出: {detected}"}


def main() -> int:
    checks = []
    rep = {"tool": "baseline_integrity", "checks": checks}

    c1 = check_csv_manifest(C.STOCKS_DIR, os.path.join(ROOT, "output", "ashare", "manifest.json"))
    checks.append({"id": "C1_csv_manifest", **c1})

    man = json.load(open(os.path.join(ROOT, "output", "ashare", "manifest.json"), encoding="utf-8"))
    checks.append({"id": "C6_tamper_selftest", **check_tamper_selftest(man["csv_hashes"], C.STOCKS_DIR)})

    c2_items = {}
    for rel in C.FROZEN_ARTIFACTS:
        p = os.path.join(ROOT, rel)
        exists = os.path.exists(p)
        head_blob = _git("rev-parse", f"HEAD:{rel}")
        disk_obj = _git("hash-object", p) if exists else None
        c2_items[rel] = {"exists": exists, "disk_eq_head": bool(exists and disk_obj == head_blob),
                         "sha256": sha256_file(p) if exists else None}
    f2 = c2_items.get("output/ashare/swing_m1_F2_realloc_full.json", {})
    f2_nums_ok = False
    if f2.get("sha256"):
        d = json.load(open(os.path.join(ROOT, "output", "ashare", "swing_m1_F2_realloc_full.json"), encoding="utf-8"))
        st = d.get("stats", {})
        f2_nums_ok = (abs(st.get("return", 0) - C.F2_LEDGER["return"]) < 1e-9
                      and abs(st.get("max_dd", 0) - C.F2_LEDGER["max_dd"]) < 1e-9
                      and st.get("rejects", {}).get("s2gate") == C.F2_LEDGER["s2gate"]
                      and st.get("trades", {}).get("BUY") == C.F2_LEDGER["buy"]
                      and st.get("trades", {}).get("SELL") == C.F2_LEDGER["sell"])
    all_eq = all(v["disk_eq_head"] for v in c2_items.values())
    checks.append({"id": "C2_frozen_artifacts", "status": "PASS" if (all_eq and f2_nums_ok) else "FAIL",
                   "per_file": c2_items, "f2_ledger_numbers_match": f2_nums_ok})

    us = {}
    for rel in C.US_FROZEN_FILES:
        p = os.path.join(ROOT, rel)
        us[rel] = {"exists": os.path.exists(p), "sha256": sha256_file(p) if os.path.exists(p) else None,
                   "disk_eq_head": _git("hash-object", p) == _git("rev-parse", f"HEAD:{rel}") if os.path.exists(p) else False}
    checks.append({"id": "C3_us_frozen", "status": "PASS" if all(v["disk_eq_head"] for v in us.values()) else "FAIL",
                   "note": "tests/test_cycle_portfolio.py 持双配置state哈希与源文件哈希锁; 此处记录当前值", "files": us})

    dirty = _git("status", "--porcelain")
    frozen_dirty = [ln for ln in dirty.splitlines() if any(rel in ln for rel in C.FROZEN_ARTIFACTS + C.US_FROZEN_FILES + ["cycle_system/swing_m1.py", "data/"])]
    checks.append({"id": "C4_worktree_frozen", "status": "PASS" if not frozen_dirty else "FAIL",
                   "dirty_lines_on_frozen_paths": frozen_dirty, "all_porcelain": dirty})

    sm = {"disk_eq_head": _git("hash-object", os.path.join(ROOT, "cycle_system/swing_m1.py")) == _git("rev-parse", "HEAD:cycle_system/swing_m1.py"),
          "sha256": sha256_file(os.path.join(ROOT, "cycle_system/swing_m1.py"))}
    checks.append({"id": "C5_swing_m1_untouched", "status": "PASS" if sm["disk_eq_head"] else "FAIL", **sm})

    # C7: R31 冻结代码哈希(manifest.code_hashes 锚): swing_m1/executor/pool/pipeline 四文件
    ch = man.get("code_hashes", {})
    c7 = {}
    for fname, want in ch.items():
        p = os.path.join(ROOT, "cycle_system", fname)
        got = sha256_file(p) if os.path.exists(p) else None
        c7[fname] = {"expected": want, "actual": got, "match": got == want}
    # 已知漂移链(显式标注, 不静默): R31 manifest 锚定的是 R30(a304606) 磁盘态 swing_m1.py;
    # R35(5b6d812)/R36(c464c51) 仅追加只读候选日志(cand_log), 不改执行语义——铁证:
    # 以当前HEAD代码独立重放F2参数集, 与R30期冻结账本逐字段零差异(f2_repro_resolution.json)。
    anchor_r30 = None
    f2_res_path = os.path.join(C.OUTPUT_DIR, "f2_repro", "f2_repro_resolution.json")
    f2_resolved = os.path.exists(f2_res_path) and json.load(open(f2_res_path, encoding="utf-8")).get("verdict") == "F2_REPRO_RESOLVED"
    try:
        blob = subprocess.run(["git", "-C", ROOT, "cat-file", "blob", "a304606:cycle_system/swing_m1.py"],
                              capture_output=True).stdout
        anchor_r30 = hashlib.sha256(blob.replace(b"\n", b"\r\n")).hexdigest() if blob else None
    except Exception:
        pass
    drift = {k: v for k, v in c7.items() if not v["match"]}
    drift_explained = (set(drift) == {"swing_m1.py"} and anchor_r30 == drift.get("swing_m1.py", {}).get("expected")
                       and f2_resolved)
    status = "PASS" if not drift else ("PASS_ANNOTATED" if drift_explained else "FAIL")
    checks.append({"id": "C7_frozen_code_hashes", "status": status,
                   "note": "data_trust_audit R31入库: V2不得修改这四文件。"
                           "漂移已定性: 锚=R30磁盘态; 当前=R35/R36只读日志演进, "
                           "F2独立重放逐字段一致⇒执行语义无漂移(f2_repro/f2_repro_resolution.json)。"
                           if drift_explained else "data_trust_audit R31入库: V2不得修改这四文件",
                   "per_file": c7})

    annotated = [c["id"] for c in checks if c["status"] == "PASS_ANNOTATED"]
    verdict = "PASS" if all(c["status"] in ("PASS", "PASS_ANNOTATED") for c in checks) else "FAIL"
    rep["verdict"] = verdict
    rep["annotated"] = annotated
    rep["_provenance"] = {
        "code_version": _git("rev-parse", "HEAD"), "branch": _git("branch", "--show-current"),
        "worktree_dirty": bool(dirty), "config_hash": C.config_hash(),
        "data_hashes": {"manifest_sha256": c1["manifest_sha256"]},
        "asof": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "mode": "SYNTHETIC", "pit_notes": ["本工具为工程验证, 非绩效声明"]}

    out_dir = os.path.join(C.OUTPUT_DIR, "baseline_integrity")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "baseline_integrity_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    lines = [f"# 阶段A基线完整性报告 — {verdict}", ""]
    for c in checks:
        lines.append(f"- **{c['id']}**: {c['status']}")
    lines += ["", f"代码版本 `{rep['_provenance']['code_version']}` ({rep['_provenance']['branch']}), "
                  f"config_hash `{rep['_provenance']['config_hash']}`, 生成于 {rep['_provenance']['generated_at']}"]
    open(os.path.join(out_dir, "baseline_integrity_report.md"), "w", encoding="utf-8").write("\n".join(lines))
    print(json.dumps({"verdict": verdict, "checks": {c["id"]: c["status"] for c in checks}}, ensure_ascii=False))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
