# -*- coding: utf-8 -*-
"""freeze_manifest — 生成 V2 规则/代码冻结锚 (独立验收第6项)。

用法: python cycle_system/ashare_v2/tools/freeze_manifest.py --date 2026-10-21 [--note "..."]
输出: output/ashare_v2/forward/FREEZE.json  (frozen_trade_date=参数日; 该日收盘后产生的信号=FORWARD)

纪律: 冻结后修改任何预测规则(阈值/权重/状态迁移/资金上限)必须另立版本(R1+)并重新冻结;
工程错误修复需在本文件 history 中登记 before/after 哈希并保持 frozen_rule_params 不变。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                   # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                             # noqa: E402

TZ = timezone(timedelta(hours=8))


def sha(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="冻结生效交易日(frozen_trade_date, YYYY-MM-DD)")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    root = os.path.dirname(_path.__file__)
    files = {os.path.relpath(p, os.path.join(C.REPO_ROOT, "cycle_system")).replace("\\", "/"): sha(p)
             for p in sorted(glob.glob(os.path.join(root, "**", "*.py"), recursive=True))}
    rule_keys = [k for k in dir(C) if k.isupper() and not k.startswith("_")]
    params = {k: getattr(C, k) for k in rule_keys}
    def git(*x):
        return subprocess.run(["git", "-C", C.REPO_ROOT] + list(x), capture_output=True,
                              text=True).stdout.strip()
    out = {"version": "DUAL_CYCLE_V2.R0", "frozen_trade_date": a.date, "note": a.note,
           "frozen_at": datetime.now(TZ).isoformat(), "head_sha": git("rev-parse", "HEAD"),
           "branch": git("branch", "--show-current"), "config_hash": C.config_hash(),
           "module_hashes": files, "frozen_rule_params": params,
           "calibrated": C.CALIBRATED,
           "policy": "预测规则变更须另立新版本(R1+)并重新冻结; 工程修复登记history且不改frozen_rule_params; "
                     "frozen_trade_date之后的信号才可标FORWARD; 之前一律REPLAY(历史回放不冒充前向)"}
    os.makedirs(os.path.join(C.OUTPUT_DIR, "forward"), exist_ok=True)
    p = os.path.join(C.OUTPUT_DIR, "forward", "FREEZE.json")
    if os.path.exists(p):
        old = json.load(open(p, encoding="utf-8"))
        print(json.dumps({"error": "FREEZE.json已存在(冻结不可覆盖), 如需重冻结请另立版本目录",
                          "existing": old["version"]}, ensure_ascii=False))
        return 1
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"frozen": out["version"], "frozen_trade_date": a.date, "head": out["head_sha"],
                      "n_files": len(files), "config_hash": out["config_hash"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
