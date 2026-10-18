# -*- coding: utf-8 -*-
"""阶段B报告: data_quality_report.json + universe 分层样例 + 行业状态截面。

用法: python cycle_system/ashare_v2/tools/phaseB_report.py
输出: output/ashare_v2/data_quality_report.json / universe_buckets_sample.json /
      industry_stage_snapshot.json (全部带溯源头, fail-closed: 锚校验不过即异常退出)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import config as C            # noqa: E402
from cycle_system.ashare_v2 import _path                  # noqa: F401,E402
from cycle_system.ashare_v2.data_portal import DataPortal  # noqa: E402
from cycle_system.ashare_v2.universe import UniverseProvider  # noqa: E402
from cycle_system.ashare_v2.industry_cycle import (IndustrySeriesBuilder, IndustryMembershipProvider,
                                                    stage_features, classify_state)  # noqa: E402


def prov():
    import subprocess
    tz = timezone(timedelta(hours=8))
    def git(*a):
        return subprocess.run(["git", "-C", C.REPO_ROOT] + list(a), capture_output=True, text=True).stdout.strip()
    return {"code_version": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
            "config_hash": C.config_hash(), "generated_at": datetime.now(tz).isoformat(),
            "mode": "SYNTHETIC"}


def main() -> int:
    out = os.path.join(C.OUTPUT_DIR)
    portal = DataPortal(data_mode=C.PIT_RESEARCH, verify_hashes=True)  # 研究模式也保持锚校验
    rep = {"_provenance": prov(), **portal.quality_report()}
    rep["_provenance"]["asof"] = rep["_provenance"]["generated_at"][:10]
    json.dump(rep, open(os.path.join(out, "data_quality_report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)

    up = UniverseProvider(portal)
    day = "2026-06-30"
    ub = up.buckets_at(day)
    ub["_provenance"] = dict(prov(), asof=day)
    # 只留计数与短清单(避免产物爆炸)
    ub["pool_stats_view"] = ub.pop("pool_stats")
    ub["pool_current"] = {k: len(v) for k, v in ub["pool_current"].items()}
    ub["data_layers"] = {k: (v if not isinstance(v, list) else len(v)) for k, v in ub["data_layers"].items()}
    json.dump(ub, open(os.path.join(out, "universe_buckets_sample.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)

    b = IndustrySeriesBuilder(portal)
    mp = IndustryMembershipProvider()
    panel = portal.price_panel()
    inds = sorted(set(str(x) for x in panel["sector"] if x))
    snap = []
    for ind in inds:
        f = stage_features(b, ind, day)
        snap.append({"industry": ind, "state": classify_state(f), "status": f.get("status"),
                     "range_pos_250d": f.get("range_pos_250d"), "trend_250d": f.get("trend_250d"),
                     "mom_12m": f.get("mom_12m"), "rel_mkt_120d": f.get("rel_mkt_120d"),
                     "max_dd_window": f.get("max_dd_window")})
    sample_code = "600438" if any(c == "600438" for c in panel["code"].unique()) else str(panel["code"].iloc[0])
    outj = {"_provenance": dict(prov(), asof=day), "day": day,
            "trust_gate": "全部状态判定基于 CURRENT_SNAPSHOT_ONLY 代理成分(PROXY_ONLY)——Strict模式不得据此出正式信号",
            "membership_example": mp.sector_of(sample_code, day),
            "industries": snap}
    json.dump(outj, open(os.path.join(out, "industry_stage_snapshot.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"data_quality": "written", "industries_n": len(snap),
                      "states": {s["state"]: sum(1 for x in snap if x["state"] == s["state"]) for s in snap}},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
