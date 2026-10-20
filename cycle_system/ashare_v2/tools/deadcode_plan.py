# -*- coding: utf-8 -*-
"""deadcode_plan — 批N完成后死券专批清单生成器(通用, 原内联脚本工具化).

missing = alive[年] ∖ (sina rows>0 ∪ bst_dead rows>0); 按 basic status 分死券/在市漏取。
用法: python poc2000_deadcode_plan.py --years 2004-2010
输出: QC/deadcode_batch_plan_{lo}_{hi}.json (codes_dead 供 poc2000_deadcode_bst.py --plan)
"""
import argparse, csv, json, os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                        # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                    # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", required=True, help="如 2004-2010")
    a = ap.parse_args()
    lo, hi = (int(x) for x in a.years.split("-"))
    basic = {r[0]: r for r in csv.reader(
        open(os.path.join(ROOT, "security_master", "basic_full_snapshot_2026-10-20.csv"), encoding="utf-8")) if len(r) > 5}
    plan = {}
    for y in range(lo, hi + 1):
        up = os.path.join(ROOT, "universe", str(y), "alive.csv")
        if not os.path.exists(up):
            print(y, "no alive.csv"); continue
        alive = [l.strip() for l in open(up, encoding="utf-8") if l.strip()]
        have = {}
        for layer in ("daily_sina", "daily_bst_dead"):
            mp = os.path.join(ROOT, layer, str(y), "manifest.json")
            if os.path.exists(mp):
                m = json.load(open(mp, encoding="utf-8"))
                for c, v in m.get("files", {}).items():
                    if v.get("rows", 0) > 0:
                        have[c] = have.get(c, 0) + 1
        # empty-note 券不再列入目标(边界事实)
        noted = set()
        for layer in ("daily_sina", "daily_bst_dead"):
            mp = os.path.join(ROOT, layer, str(y), "manifest.json")
            if os.path.exists(mp):
                m = json.load(open(mp, encoding="utf-8"))
                noted.update(c for c, v in m.get("files", {}).items()
                             if str(v.get("note", "")).startswith("no_bar"))
        missing = [c for c in alive if c not in have and c not in noted]
        dead = [c for c in missing if basic.get(c, [""] * 6)[5] == "0"]
        live_fail = [c for c in missing if c not in dead]
        plan[y] = {"missing_total": len(missing), "dead_code": len(dead),
                   "alive_but_fail": len(live_fail), "codes_dead": dead,
                   "codes_live_fail": live_fail}
        print(y, plan[y]["missing_total"], "=dead", len(dead), "+live_fail", len(live_fail), flush=True)
    out = os.path.join(ROOT, "QC", f"deadcode_batch_plan_{lo}_{hi}.json")
    tmp = out + ".tmp"
    json.dump({str(k): v for k, v in plan.items()}, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, out)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
