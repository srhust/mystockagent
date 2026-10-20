# -*- coding: utf-8 -*-
"""poc2000_deadcode_bst — 退市券价格专批(baostock 独有通道能力, sina 结构性无源已证).

清单: QC/deadcode_batch_plan_{lo}_{hi}.json(由 alive∖sina 差集生成)。
卫生: 每50券主动relogin(防僵尸会话); 单券15s线程有界; 连续3券HANG=通道阻断 rc7 速退
     (不硬拖, 换低峰窗重跑; 续传幂等)。落盘/manifest/原子/审批绑定复用 sina 批器。
coverage 更新: union(sina, bst_dead) 重算并写 coverage_union_{Y}.json(批次门=并集≥0.85)。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                        # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                    # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from poc2000_m4_batch import verify_approval                                     # noqa: E402
import threading                                                                   # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
COLS = "date,open,high,low,close,preclose,volume,amount,turn,tradestatus,isST"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def pull_bst(bs, code: str, y: int, bound=15):
    box = {}

    def _w():
        try:
            rs = bs.query_history_k_data_plus(code, COLS, start_date=f"{y}-01-01",
                                             end_date=f"{y}-12-31", frequency="d", adjustflag="3")
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            box["v"] = (rs.error_code, rs.error_msg, rows)
        except Exception as e:
            box["v"] = ("EXC", type(e).__name__, [])

    t = threading.Thread(target=_w, daemon=True)
    t.start()
    t.join(bound)
    if "v" not in box:
        return None, "HANG"                       # 有界弃→调用侧连续HANG计数
    code_e, msg, rows = box["v"]
    if code_e != "0":
        return None, f"ERR:{code_e}:{(msg or '')[:30]}"
    return rows, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="QC/deadcode_batch_plan_*.json")
    a = ap.parse_args()
    plan_p = os.path.join(ROOT, a.plan) if not os.path.isabs(a.plan) else a.plan
    plan = json.load(open(plan_p, encoding="utf-8"))
    years = [int(y) for y in plan.keys()]
    verify_approval(years)
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        print("login fail:", lg.error_msg)
        return 7
    hang_streak = 0
    processed_since_login = 0
    out_reps = {}
    for y in years:
        codes = plan[str(y)]["codes_dead"]
        out = os.path.join(ROOT, "daily_bst_dead", str(y))
        os.makedirs(out, exist_ok=True)
        man_p = os.path.join(out, "manifest.json")
        man = json.load(open(man_p, encoding="utf-8")) if os.path.exists(man_p) else {"year": y, "files": {}}
        ok = skip = fail = empty = 0
        for code in codes:
            rec = man["files"].get(code)
            dest = os.path.join(out, f"{code}.csv")
            if rec and rec.get("rows", 0) > 0 and os.path.exists(dest) and sha256(dest) == rec.get("sha256"):
                skip += 1
                continue
            if processed_since_login >= 50:
                bs.logout(); bs.login(); processed_since_login = 0; hang_streak = 0
            rows, err = pull_bst(bs, code, y)
            processed_since_login += 1
            if err == "HANG":
                hang_streak += 1
                fail += 1
                if hang_streak >= 8:   # 18:20实弹修正: baostock=冷启动节流形态(前段连挂后段可成, 8/8成功实证), 容忍从3放宽到8并配合sleep稀释
                    print(f"CHANNEL_BLOCKED 连续8HANG @y{y} {code}; 已存 续传友好退出")
                    mtmp = man_p + ".tmp"
                    json.dump(man, open(mtmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
                    os.replace(mtmp, man_p)
                    bs.logout()
                    return 7
                time.sleep(10)         # 挂后稀释: 给服务端窗口恢复时间
                continue
            if err:
                fail += 1
                man.setdefault("errors", []).append(f"{code}:{err}")
                hang_streak = 0
                continue
            hang_streak = 0
            if not rows:
                empty += 1
                man["files"][code] = {"rows": 0, "note": "no_bar_in_year(停牌/边界)"}
                continue
            tmp = dest + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(COLS.split(","))
                w.writerows(rows)
            first, last = rows[0][0], rows[-1][0]
            if not (f"{y}-01-01" <= first <= last <= f"{y}-12-31"):
                os.remove(tmp)
                fail += 1
                continue
            os.replace(tmp, dest)
            man["files"][code] = {"rows": len(rows), "sha256": sha256(dest),
                                  "range_first": first, "range_last": last}
            ok += 1
            time.sleep(1.0)            # 成功段pacing(节流形态防御)
            if (ok + skip) % 50 == 0:
                mtmp = man_p + ".tmp"
                json.dump(man, open(mtmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
                os.replace(mtmp, man_p)
        man["generated"] = datetime.now().isoformat(timespec="seconds")
        man["stats"] = {"ok": ok, "skip": skip, "fail": fail, "empty": empty}
        mtmp = man_p + ".tmp"
        json.dump(man, open(mtmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        os.replace(mtmp, man_p)
        # 并集 coverage
        sm = json.load(open(os.path.join(ROOT, "daily_sina", str(y), "manifest.json"), encoding="utf-8"))
        alive = [l.strip() for l in open(os.path.join(ROOT, "universe", str(y), "alive.csv")) if l.strip()]
        got = sum(1 for c in alive
                  if sm["files"].get(c, {}).get("rows", 0) > 0 or man["files"].get(c, {}).get("rows", 0) > 0)
        cov_u = round(got / max(1, len(alive)), 4)
        rep = {"year": y, "union_got": got, "alive": len(alive), "coverage_union": cov_u,
               "gate": "PASS" if cov_u >= 0.85 else "GAP_REMAINS",
               "sina_only": round(sum(1 for c in alive if sm["files"].get(c, {}).get("rows", 0) > 0) / len(alive), 4),
               "bst_dead_added": ok + skip}
        tmp2 = os.path.join(ROOT, "QC", f"coverage_union_{y}.json.tmp")
        json.dump(rep, open(tmp2, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp2, os.path.join(ROOT, "QC", f"coverage_union_{y}.json"))
        out_reps[y] = rep
        print(y, rep, flush=True)
    bs.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
