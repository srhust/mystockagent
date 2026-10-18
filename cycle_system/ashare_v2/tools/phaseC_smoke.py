# -*- coding: utf-8 -*-
"""阶段C真实数据冒烟: 双引擎在 R31 锚定面板上完整构建并出当日候选。

用法: python cycle_system/ashare_v2/tools/phaseC_smoke.py
输出: output/ashare_v2/engine_smoke_realdata.json (含状态分布/候选样例/strict闸门报告/溯源头)
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import config as C                          # noqa: E402
from cycle_system.ashare_v2 import _path                                # noqa: F401,E402
from cycle_system.ashare_v2.data_portal import DataPortal               # noqa: E402
from cycle_system.ashare_v2.fast_v import FastVEngine                   # noqa: E402
from cycle_system.ashare_v2.industry_cycle import IndustryMembershipProvider, IndustrySeriesBuilder  # noqa: E402
from cycle_system.ashare_v2.long_cycle import LongCycleEngine           # noqa: E402


def prov(asof):
    import subprocess
    tz = timezone(timedelta(hours=8))
    def git(*a):
        return subprocess.run(["git", "-C", C.REPO_ROOT] + list(a), capture_output=True, text=True).stdout.strip()
    return {"code_version": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
            "config_hash": C.config_hash(), "asof": asof, "mode": "SYNTHETIC",
            "note": "冒烟=编译/运行验证, 非预测有效", "generated_at": datetime.now(tz).isoformat()}


def main() -> int:
    day = "2026-06-30"
    portal = DataPortal(data_mode=C.PIT_RESEARCH)
    b = IndustrySeriesBuilder(portal)
    mp = IndustryMembershipProvider()
    lc = LongCycleEngine(portal, b, mp, data_mode=C.PIT_RESEARCH)
    lc.build()
    fv = FastVEngine(portal, data_mode=C.PIT_RESEARCH)
    fv.build()
    lc_states = {ind: lc.industry_state(ind, day) for ind in lc._states}
    fv_states = Counter(fv.state_at(c, day) for c in fv._states)
    cand_lc = lc.candidates(day)
    cand_fv = fv.candidates(day)
    lc_strict = LongCycleEngine(portal, b, mp, data_mode=C.PIT_STRICT)
    lc_strict.build()
    strict_empty = lc_strict.candidates(day)
    out = {
        "_provenance": prov(day),
        "industry_state_dist": dict(Counter(lc_states.values())),
        "industry_states": lc_states,
        "fast_v_state_dist": dict(fv_states),
        "candidates": {
            "long_cycle": {"n": len(cand_lc), "entry_kinds": dict(Counter(c.entry_kind for c in cand_lc)),
                            "sample": [_rc(c) for c in cand_lc][:3]},
            "fast_v": {"n": len(cand_fv), "stages": dict(Counter(c.cycle_stage for c in cand_fv)),
                        "sample": [_rc(c) for c in cand_fv][:3]},
        },
        "strict_gate": {"long_cycle_candidates": len(strict_empty), "report_tail": lc_strict.strict_gate_report[-1]},
        "engine_build_secs": "见运行日志",
        "discipline": {
            "no_hindsight": "状态=单程时序前缀函数(test_prefix_determinism锁)",
            "lc_no_fixed_day_exit": True, "fv_expire_engine_only": True,
            "claims": "候选≠预测有效; 评分未校准(D段前不排名)",
        },
    }
    p = os.path.join(C.OUTPUT_DIR, "engine_smoke_realdata.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"lc_states": out["industry_state_dist"], "fv": out["fast_v_state_dist"],
                      "cand_lc": len(cand_lc), "cand_fv": len(cand_fv), "strict": len(strict_empty)},
                     ensure_ascii=False))
    return 0


def _rc(c):
    d = {k: (v if not hasattr(v, "isoformat") else str(v)) for k, v in c.__dict__.items()}
    d["key_evidence"] = [e.__dict__ for e in d.get("key_evidence", [])]
    return d


if __name__ == "__main__":
    sys.exit(main())
