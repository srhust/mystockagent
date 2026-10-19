# -*- coding: utf-8 -*-
"""strict gate 运行时报告: 四阻断条件真实数据探针 → output/ashare_v2/independent/strict_gate.json。

用法: python cycle_system/ashare_v2/tools/strict_gate_report.py [day]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                   # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                             # noqa: E402
from cycle_system.ashare_v2 import strict_gate as SG                       # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                   # noqa: E402
from cycle_system.ashare_v2.industry_cycle import (IndustryMembershipProvider,  # noqa: E402
                                                   IndustrySeriesBuilder)
from cycle_system.ashare_v2.long_cycle import LongCycleEngine               # noqa: E402
from cycle_system.ashare_v2.universe import UniverseProvider                # noqa: E402

TZ = timezone(timedelta(hours=8))


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-06-30"
    portal = DataPortal(data_mode=C.PIT_RESEARCH)
    mp = IndustryMembershipProvider()
    buckets = UniverseProvider(portal).buckets_at(day)
    lc_s = LongCycleEngine(portal, IndustrySeriesBuilder(portal), mp, data_mode=C.PIT_STRICT)
    lc_s.build()
    cand = lc_s.candidates(day)
    rep = SG.evaluate(portal=portal, membership=mp, universe_buckets=buckets,
                      lc_strict_candidates=cand)
    rep["_provenance"] = {"asof": day, "generated_at": datetime.now(TZ).isoformat(),
                          "mode": "STATIC_RESEARCH_PROBE(历史回放, 非前向信号)"}
    ok = (rep["probe"].get("strict_blocked_confirmed") is True
          and rep["probe"].get("market_wide_status") == "DATA_BLOCKED"
          and rep["probe"].get("sw_dataset_status") == "PIT_UNVERIFIED"
          and rep["probe"].get("sw_trust_sample") in ("SW_PARTIAL", "UNKNOWN")
          and rep["probe"].get("fundamental_blocked") == rep["probe"].get("fundamental_total") > 0
          and rep["probe"].get("survivorship_note_present") is True)
    rep["four_gates_confirmed"] = ok
    outdir = os.path.join(C.OUTPUT_DIR, "independent")
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, "strict_gate.json")
    json.dump(rep, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"four_gates_confirmed": ok, "probe": rep["probe"]}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
