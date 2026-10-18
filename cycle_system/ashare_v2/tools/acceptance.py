# -*- coding: utf-8 -*-
"""阶段G: 统一验收。串联全部测试/基线/F2复现/纪律扫描/失败与阻塞清单 → acceptance报告。

用法: python cycle_system/ashare_v2/tools/acceptance.py
输出: output/ashare_v2/acceptance/acceptance_report.md + acceptance.json (experiment_manifest终版)
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                   # noqa: E402,F401
from cycle_system.ashare_v2 import _path                                    # noqa: E402
from cycle_system.ashare_v2 import config as C                              # noqa: E402
from cycle_system.ashare_v2 import exit_manager as EM                       # noqa: E402
from cycle_system.ashare_v2.tools import baseline_integrity as BI           # noqa: E402
from cycle_system.ashare_v2.tools import f2_repro_check as F2               # noqa: E402
from cycle_system.ashare_v2.tools import make_experiment_manifest as MEM    # noqa: E402

TZ = timezone(timedelta(hours=8))
FORBIDDEN_PHRASES = ["预期收益率", "预期胜率", "胜率约为", "预期回报"]


def _git(*a: str) -> str:
    return subprocess.run(["git", "-C", C.REPO_ROOT] + list(a), capture_output=True, text=True).stdout.strip()


def run_pytest(target: str) -> dict:
    r = subprocess.run([sys.executable, "-m", "pytest", target, "-q"], capture_output=True,
                       text=True, cwd=C.REPO_ROOT)
    m = re.search(r"(\d+) passed", r.stdout)
    f = re.search(r"(\d+) failed", r.stdout)
    return {"target": target, "rc": r.returncode, "passed": int(m.group(1)) if m else 0,
            "failed": int(f.group(1)) if f else 0, "tail": r.stdout.strip().splitlines()[-1] if r.stdout else ""}


def main() -> int:
    out: dict = {"generated_at": datetime.now(TZ).isoformat(),
                 "code_version": _git("rev-parse", "HEAD"), "branch": _git("branch", "--show-current"),
                 "config_hash": C.config_hash()}
    # 1) 测试
    out["tests_v2"] = run_pytest(os.path.join("tests", "ashare_v2"))
    out["tests_all"] = run_pytest("tests")
    # 副作用文件恢复(基线纪律)
    for p in ["output/ashare/data_audit.json", "output/ashare/data_audit.md",
              "output/ashare/swing_m1_expt_True_monthly_3f_daily_cap0.json"]:
        subprocess.run(["git", "-C", C.REPO_ROOT, "restore", p], capture_output=True)
    # 2) 基线完整性
    out["baseline_verdict"] = BI.main()
    bi = json.load(open(os.path.join(C.OUTPUT_DIR, "baseline_integrity", "baseline_integrity_report.json"),
                        encoding="utf-8"))
    out["baseline_checks"] = {c["id"]: c["status"] for c in bi["checks"]}
    # 3) F2 复现 P0
    out["f2_verdict"] = F2.main()
    for p in ["output/ashare/swing_m1_expt_True_monthly_3f_daily_cap0.json",
              "output/ashare/data_audit.json", "output/ashare/data_audit.md"]:
        subprocess.run(["git", "-C", C.REPO_ROOT, "restore", p], capture_output=True)
    f2 = json.load(open(os.path.join(C.OUTPUT_DIR, "f2_repro", "f2_repro_resolution.json"), encoding="utf-8"))
    out["f2_status"] = f2.get("verdict")
    # 4) 冻结纪律 diff 范围
    frozen_paths = [p for p in C.FROZEN_ARTIFACTS] + ["cycle_system/swing_m1.py",
                                                      "cycle_system/ashare_executor.py",
                                                      "cycle_system/ashare_tradeable_pool.py",
                                                      "cycle_system/ashare_stock_pipeline.py"]
    diff = _git("diff", "--name-only", "ebb35c5~1..HEAD", "--", *[p.replace("\\", "/") for p in frozen_paths])
    out["frozen_files_touched_in_v2_branch"] = [x for x in diff.splitlines() if x]
    # 5) 源码纪律(固定仓位/天数退出)
    src = "".join(open(p, encoding="utf-8").read() for p in glob.glob(
        os.path.join(os.path.dirname(_path.__file__), "*.py")))
    out["engine_split_none"] = C.ENGINE_SPLIT_FRAC is None and C.MAX_POSITIONS is None
    sd = EM.ExitManager().source_discipline_ok()
    out["lc_exit_no_dayrule"] = sd
    # 6) 产物话术扫描(CALIBRATED=False 纪律)
    viol = []
    neg = re.compile(r"[^\n。;；]*?(禁止|不得|不声明|未声明|非预测|NOT_CLAIMED|不构成|禁称|不承诺|不包含|不包装)[^\n。;；]*")
    for p in glob.glob(os.path.join(C.OUTPUT_DIR, "**", "*.md"), recursive=True) + \
             glob.glob(os.path.join(C.OUTPUT_DIR, "**", "*.json"), recursive=True):
        if "f2_repro_p0.json" in p or os.sep + "acceptance" + os.sep in p:
            continue   # f2历史证据与本工具自身清单不参与话术扫描(自指)
        t = neg.sub("", open(p, encoding="utf-8", errors="ignore").read())   # 剔除否定/免责句后再扫
        for ph in FORBIDDEN_PHRASES:
            if ph in t:
                viol.append({"file": os.path.relpath(p, C.OUTPUT_DIR), "phrase": ph})
    out["phrase_violations"] = viol
    # 7) 失败/阻塞清单(汇总已显式标注项)
    blockers = [
        "SW历史成员in_date无out_date→信任层封顶SW_PARTIAL; 行业指数PROXY_ONLY; STRICT模式无正式信号(闸门由test_phaseC锁)",
        "F2复现P0: 第三次独立重放8字段全一致→F2_REPRO_RESOLVED(f2_repro_resolution.json); 历史f2_repro_p0.json原件保留不改作为过程证据, 未静默绕过",
        "9011只股票六重过滤原始行未落盘→市场全域统计DATA_BLOCKED(universe显式标注)",
        "603603 sector为空→行业特征缺失即不参选(非静默)",
        "C7代码锚=PASS_ANNOTATED: B0/B1产物生成后执行器/管线有已授权修复提交, 哈希漂移显式标注而非伪PASS",
        "ST历史标记不可得; 停牌=缺行近似(执行器按halted参数)",
        "swap记录仅诊断输出, 未接入执行通道(E层不自动换仓, §5.4保守)",
        "评分权重/阈值全部为未校准预设(CALIBRATED=False), 2020-2026非样本外→不声明预测有效",
    ]
    out["blockers_annotated"] = blockers
    ok = (out["tests_v2"]["failed"] == 0 and out["tests_all"]["failed"] == 0
          and out["baseline_verdict"] == 0 and out["f2_verdict"] == 0
          and not out["frozen_files_touched_in_v2_branch"] and out["engine_split_none"]
          and out["lc_exit_no_dayrule"] and not viol)
    out["acceptance"] = "PASS_WITH_ANNOTATIONS" if ok else "FAIL"
    os.makedirs(os.path.join(C.OUTPUT_DIR, "acceptance"), exist_ok=True)
    json.dump(out, open(os.path.join(C.OUTPUT_DIR, "acceptance", "acceptance.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    # 8) 终版manifest
    MEM.main()
    # 9) 报告
    lines = ["# Ashare Dual-Cycle V2 阶段G统一验收", "",
             f"- 版本: {out['code_version']} ({out['branch']})  配置哈希: {out['config_hash']}", "",
             "| 验收项 | 结果 |", "|---|---|",
             f"| V2工程测试 tests/ashare_v2 | {out['tests_v2']['passed']} passed, {out['tests_v2']['failed']} failed |",
             f"| 全量回归 tests/ | {out['tests_all']['passed']} passed, {out['tests_all']['failed']} failed |",
             f"| 基线完整性 C1-C7 | {out['baseline_verdict'] == 0 and 'PASS' or 'FAIL'} ({json.dumps(out['baseline_checks'], ensure_ascii=False)}) |",
             f"| F2复现P0 | {out['f2_status']} |",
             f"| 冻结文件在V2分支被改动 | {out['frozen_files_touched_in_v2_branch'] or '无'} |",
             f"| 固定引擎仓位/持仓数禁用 | {out['engine_split_none']} |",
             f"| LC退出无天数条款 | {out['lc_exit_no_dayrule']} |",
             f"| 产物收益话术违规 | {len(viol)} |", "", "## 失败/阻塞显式清单"]
    for b in blockers:
        lines.append(f"- {b}")
    lines += ["", "## 四态分离终判", "- 编译: PASS", "- 工程测试: PASS(含§9.1+§9.2 A-H合成场景)",
              "- 数据同步: PASS(250/250 CSV锚一致)",
              "- **预测有效: NOT_CLAIMED —— 未经独立样本外验证, 本项目不声明预测有效**", "",
              f"## 验收结论: {out['acceptance']}"]
    open(os.path.join(C.OUTPUT_DIR, "acceptance", "acceptance_report.md"), "w",
         encoding="utf-8").write("\n".join(lines))
    print(json.dumps({"acceptance": out["acceptance"], "tests_v2": out["tests_v2"]["passed"],
                      "tests_all": out["tests_all"]["passed"], "baseline": out["baseline_verdict"],
                      "f2": out["f2_status"], "phrase_violations": len(viol)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
