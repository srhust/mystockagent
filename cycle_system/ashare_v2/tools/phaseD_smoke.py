# -*- coding: utf-8 -*-
"""阶段D真实数据链路冒烟: 候选→合并→评分→贪心分配 全链(单日截面)。

用法: python cycle_system/ashare_v2/tools/phaseD_smoke.py
输出: output/ashare_v2/allocation_smoke_realdata.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import capital_allocator as K          # noqa: E402
from cycle_system.ashare_v2 import config as C                     # noqa: E402
from cycle_system.ashare_v2 import _path                            # noqa: F401,E402
from cycle_system.ashare_v2 import opportunity_ranker as R          # noqa: E402
from cycle_system.ashare_v2 import signal_merge as M                # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal           # noqa: E402
from cycle_system.ashare_v2.fast_v import FastVEngine               # noqa: E402
from cycle_system.ashare_v2.industry_cycle import (IndustryMembershipProvider,  # noqa: E402
                                                   IndustrySeriesBuilder)
from cycle_system.ashare_v2.long_cycle import LongCycleEngine       # noqa: E402


def main() -> int:
    day = "2026-06-30"
    portal = DataPortal(data_mode=C.PIT_RESEARCH)
    panel = portal.price_panel()
    lc = LongCycleEngine(portal, IndustrySeriesBuilder(portal), IndustryMembershipProvider(),
                         data_mode=C.PIT_RESEARCH)
    lc.build()
    fv = FastVEngine(portal, data_mode=C.PIT_RESEARCH)
    fv.build()
    merged = M.merge_candidates(lc.candidates(day), fv.candidates(day))
    sec_of = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()
    upto = panel[panel["date"] <= day].sort_values("date")
    pxmap, volmap, agemap = {}, {}, {}
    for c, sub in upto.groupby("code"):
        s = sub["close_adj"].values
        if len(s) >= 25:
            pxmap[c] = float(s[-1])
            rr = np.diff(s[-21:]) / s[-21:-1]
            volmap[c] = float(np.nanstd(rr)) if np.isfinite(rr).any() else None
            agemap[c] = {"amt20": float(sub["amount"].values[-20:].mean()), "freshness": 1.0}
    for m in merged:
        m["age_days"] = {r.engine: {**agemap.get(m["code"], {}),
                                   **{"amt20": agemap.get(m["code"], {}).get("amt20")}}
                        for r in m["records"]}
    scored = [R.score_merged(m) for m in merged]
    scored = [s for s in scored if s["code"] in pxmap and volmap.get(s["code"])]
    plan = K.allocate(scored, [], cash=C.NAV0, nav=C.NAV0,
                      ctx={"px": pxmap, "vol20": volmap, "industry": sec_of, "budget_state": "NORMAL"})
    tz = timezone(timedelta(hours=8))

    def git(*a):
        import subprocess
        return subprocess.run(["git", "-C", C.REPO_ROOT] + list(a), capture_output=True, text=True).stdout.strip()
    out = {"_provenance": {"code_version": git("rev-parse", "HEAD"), "config_hash": C.config_hash(),
                            "asof": day, "mode": "SYNTHETIC", "generated_at": datetime.now(tz).isoformat(),
                            "note": "冒烟=链路可运行验证, 非预测有效; NAV口径=影子基20000"},
           "n_candidates": {"merged": len(merged), "scored_with_pxvol": len(scored)},
           "top_scores": sorted([{"code": s["code"], "pts": s["score_points"], "engines": s["engines"]}
                                  for s in scored], key=lambda x: -x["pts"])[:6],
           "plan": {k: plan[k] for k in ("buys", "rejects", "swaps", "cash_conservation", "engine_funding",
                                           "engine_frac", "industry_weights_after", "position_count",
                                           "risk_budget_state", "note", "calibration_note")}}
    p = os.path.join(C.OUTPUT_DIR, "allocation_smoke_realdata.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"merged": len(merged), "scored": len(scored), "buys": len(plan["buys"]),
                      "rejects": len(plan["rejects"]), "cash_ok": plan["cash_conservation"]["ok"],
                      "engine_frac": plan["engine_frac"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
