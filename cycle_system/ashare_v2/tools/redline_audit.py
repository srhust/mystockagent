# -*- coding: utf-8 -*-
"""redline_audit — 审计红线遵守的机器可验证快照(替代口头承诺)。

核验: R1 FORMAL_ADMIT.json不存在 · R2 正式绩效账本/正式state不存在(准入前零写入)
· R3 16预测模块磁盘哈希与FREEZE基线逐项匹配(runner/portfolio未因本审计被改)
· R4 冻结主CSV未改写(mtime+行数) · R5 链verify+head seal · R6 FORWARD域无formal记录。
输出 independent/redline_audit.json。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                              # noqa: E402
from cycle_system.ashare_v2 import forward_ledger as FL                     # noqa: E402
from cycle_system.ashare_v2.tools import forward_shadow as FS               # noqa: E402

FWD = os.path.join(C.OUTPUT_DIR, "forward")
out: dict = {"day": "2026-10-19"}
out["R1_FORMAL_ADMIT_absent"] = {"path_exists": os.path.exists(FS.ADMIT_P)}
out["R1_FORMAL_ADMIT_absent"]["ok"] = not out["R1_FORMAL_ADMIT_absent"]["path_exists"]
led = os.path.join(FWD, "ledger", f"ledger_{FS.LEDGER_NAME}.jsonl")
st_info = {"ledger_exists": os.path.exists(led)}
if os.path.exists(FS.STATE_P):
    stc = json.load(open(FS.STATE_P, encoding="utf-8"))
    # 允许的唯一形态: 修复前REPLAY plan残留(plans仅10-16, 无成交态)——plan级REPLAY非FORWARD正式账本
    st_info.update({"state_exists": True,
                    "state_is_replay_leftover_only": (list(stc.get("plans", {})) == ["2026-10-16"]
                                                      and not stc.get("ledger_trading_day")
                                                      and not stc.get("positions")),
                    "state_plans": list(stc.get("plans", {})), "state_trading_day": stc.get("ledger_trading_day")})
else:
    st_info["state_exists"] = False
st_info["ok"] = not st_info["ledger_exists"] and (not st_info["state_exists"] or st_info.get("state_is_replay_leftover_only"))
out["R2_formal_state_and_ledger"] = st_info
base_p = os.path.join(FWD, "FREEZE_ADDENDUM_R0.3.json")
mods = {}
if os.path.exists(base_p):
    pkg_dir = os.path.dirname(os.path.abspath(C.__file__))
    root_dir = os.path.dirname(os.path.dirname(pkg_dir))
    base = json.load(open(base_p, encoding="utf-8"))
    pred = set(FS.PREDICTION_MODULES)
    for rel, want in (base.get("module_hashes") or {}).items():
        p = os.path.join(pkg_dir, rel.replace("ashare_v2/", "", 1)) if rel.startswith("ashare_v2/") else os.path.join(root_dir, rel)
        have = hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None
        mods[rel] = {"match": have == want}
    pred_mis = [r for r, v in mods.items() if not v["match"] and not r.startswith("ashare_v2/tools/")
                and r.replace("ashare_v2/", "") in pred]
    warn_only = [r for r, v in mods.items() if not v["match"] and r.startswith("ashare_v2/tools/")]
    out["R3_prediction_modules_match_R03_baseline"] = {
        "prediction_checked": sum(1 for r in mods if not r.startswith("ashare_v2/tools/")),
        "prediction_mismatch": pred_mis, "tools_domain_changes_warn_only": warn_only}
    out["R3_prediction_modules_match_R03_baseline"]["ok"] = not pred_mis
else:
    out["R3_prediction_modules_match_R03_baseline"] = {"ok": False, "note": "基线文件缺失"}
import pandas as pd
from cycle_system.ashare_v2.data_portal import DataPortal
try:
    panel = DataPortal(data_mode=C.PIT_STRICT).price_panel()   # strict模式内建R31 manifest+逐文件sha256校验
    out["R4_frozen_panel_intact"] = {"strict_manifest_verified": True, "rows": int(len(panel)),
                                     "max_date": str(pd.Timestamp(panel["date"].max()).date())}
except Exception as e:  # noqa: BLE001
    out["R4_frozen_panel_intact"] = {"strict_manifest_verified": False, "error": str(e)[:200]}
out["R4_frozen_panel_intact"]["ok"] = bool(out["R4_frozen_panel_intact"].get("strict_manifest_verified"))
rows = FL.read_all(FS.CHAIN_P) if os.path.exists(FS.CHAIN_P) else []
out["R5_chain"] = {"n": len(rows), "verify": FL.verify(FS.CHAIN_P)["ok"] if rows else None,
                   "formal_gate_records": sum(1 for r in rows if r.get("formal_gate") == "FORMAL")}
out["R5_chain"]["ok"] = bool(rows and out["R5_chain"]["verify"]) and out["R5_chain"]["formal_gate_records"] == 0
out["R6_overall"] = {"ok": all(v.get("ok", False) for v in out.values() if isinstance(v, dict))}
op = os.path.join(C.OUTPUT_DIR, "independent", "redline_audit.json")
json.dump(out, open(op, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
return_ = 0 if out["R6_overall"]["ok"] else 1
sys.exit(return_)
