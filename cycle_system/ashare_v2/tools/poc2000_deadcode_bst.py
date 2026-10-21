# -*- coding: utf-8 -*-
"""poc2000_deadcode_bst V2 — 退市券价格专批·全生命史版(baostock 独有通道).

V2 关键升级(21:47 实测定案: sina purge=全历史级, 死券真实量=券数×存续年, 单年查询请求量爆炸):
  每券一次 query 1998-2026 全史 → 按年拆分写入 daily_bst_dead/{Y}/ → 请求量从券×年降到券数。
清单: QC/deadcode_batch_plan_*.json 的 codes_dead 跨并集(去重)。
卫生: 有界15s线程/连续8HANG=rc7速退+flush/挂后10s稀释/成功pacing 1.0s/每50券relogin;
原子tmp+sha入每年manifest; 续传=某券其在plan全部年已有rows>0&sha匹配则skip。
收尾: coverage_union 每年重算(sina∪bst)。审批绑定复用。
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
import threading                                                                 # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
COLS = "date,open,high,low,close,preclose,volume,amount,turn,tradestatus,isST"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def pull_life(bs, code: str, lo: int, hi: int, bound=40):
    """一次查全生命史(1998..2026), 返回date开头行列表。"""
    box = {}

    def _w():
        try:
            rs = bs.query_history_k_data_plus(code, COLS, start_date=f"{lo}-01-01",
                                              end_date=f"{hi}-12-31", frequency="d", adjustflag="3")
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
        return None, "HANG"
    code_e, msg, rows = box["v"]
    if code_e != "0":
        return None, f"ERR:{code_e}:{(msg or '')[:30]}"
    return rows, ""


def load_man(y: int):
    p = os.path.join(ROOT, "daily_bst_dead", str(y), "manifest.json")
    man = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"year": y, "files": {}}
    man.setdefault("adjust_basis", "baostock_raw_同基含tradestatus_isST")
    return man, p


def flush(man, p):
    tmp = p + ".tmp"
    json.dump(man, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    os.replace(tmp, p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    a = ap.parse_args()
    plan_p = os.path.join(ROOT, a.plan) if not os.path.isabs(a.plan) else a.plan
    plan = json.load(open(plan_p, encoding="utf-8"))
    years = sorted(int(y) for y in plan.keys())
    verify_approval(years)
    # 券→其需补的年集合
    need = {}
    for y in years:
        for c in plan[str(y)].get("codes_dead", []):
            need.setdefault(c, set()).add(y)
    codes = sorted(need)
    print(f"codes={len(codes)} code-years={sum(len(v) for v in need.values())} years={years[0]}..{years[-1]}")
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        print("login fail:", lg.error_msg)
        return 7
    hang_streak = 0
    done_codes = 0
    mans = {y: load_man(y) for y in years}
    for code, ys in need.items():
        # skip: 该券全部目标年已有rows>0+sha匹配
        if all(mans[y][0]["files"].get(code, {}).get("rows", 0) > 0
               and os.path.exists(os.path.join(ROOT, "daily_bst_dead", str(y), f"{code}.csv"))
               and sha256(os.path.join(ROOT, "daily_bst_dead", str(y), f"{code}.csv")) == mans[y][0]["files"][code].get("sha256")
               for y in ys):
            continue
        if done_codes and done_codes % 50 == 0:
            bs.logout(); bs.login()
            for y in years:
                mans[y] = load_man(y)
        lo, hi = min(ys), max(ys)
        rows, err = pull_life(bs, code, lo, hi)
        done_codes += 1
        if err == "HANG":
            hang_streak += 1
            if hang_streak >= 8:
                for y in years:
                    flush(*mans[y])
                print(f"CHANNEL_BLOCKED @ {code}; 续传友好退出")
                bs.logout()
                return 7
            time.sleep(10)
            continue
        hang_streak = 0
        if err:
            # 09:26修复: 服务端坏流(解码/decompress/接收异常)与HANG同格计入通道容忍,
            # 否则坏流走ERR路径空烧全清单(V2实跑120/258后坏流期空转118券的教训)
            if "EXC" in err and any(k in err for k in ("UnicodeDecode", "OSError", "Error", "Exception")):
                hang_streak += 1
                if hang_streak >= 8:
                    for y in years:
                        flush(*mans[y])
                    print(f"CHANNEL_BLOCKED(badstream) @ {code}; 续传友好退出")
                    bs.logout()
                    return 7
                time.sleep(10)
                continue
            for y in ys:
                mans[y][0].setdefault("errors", []).append(f"{code}:{err}")
            continue
        if not rows:
            for y in ys:
                mans[y][0]["files"][code] = {"rows": 0, "note": "no_bar_in_life(边界事实)"}
            continue
        by_year = {}
        for r in rows:
            yy = int(r[0][:4])
            if yy in ys:
                by_year.setdefault(yy, []).append(r)
        for y, yrows in by_year.items():
            out = os.path.join(ROOT, "daily_bst_dead", str(y))
            os.makedirs(out, exist_ok=True)
            dest = os.path.join(out, f"{code}.csv")
            tmp = dest + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(COLS.split(","))
                w.writerows(yrows)
            first, last = yrows[0][0], yrows[-1][0]
            if not (f"{y}-01-01" <= first <= last <= f"{y}-12-31"):
                os.remove(tmp)
                continue
            os.replace(tmp, dest)
            mans[y][0]["files"][code] = {"rows": len(yrows), "sha256": sha256(dest),
                                         "range_first": first, "range_last": last,
                                         "note": "life_query_split_V2"}
        # 查询区间内无bar的年(未上市/已退市边界)记no_bar
        for y in ys - set(by_year):
            mans[y][0]["files"][code] = {"rows": 0, "note": "outside_life_in_query(y)"}
        if done_codes % 20 == 0:
            for y in years:
                flush(*mans[y])
            print(f"progress codes={done_codes}/{len(codes)} @ {datetime.now().isoformat(timespec='seconds')}", flush=True)
        time.sleep(1.0)
    for y in years:
        mans[y][0]["generated"] = datetime.now().isoformat(timespec="seconds")
        flush(*mans[y])
    # union coverage 全重算
    for y in years:
        sm_p = os.path.join(ROOT, "daily_sina", str(y), "manifest.json")
        sm = json.load(open(sm_p, encoding="utf-8")) if os.path.exists(sm_p) else {"files": {}}
        alive = [l.strip() for l in open(os.path.join(ROOT, "universe", str(y), "alive.csv")) if l.strip()]
        got = sum(1 for c in alive
                  if sm["files"].get(c, {}).get("rows", 0) > 0 or mans[y][0]["files"].get(c, {}).get("rows", 0) > 0)
        cov_u = round(got / max(1, len(alive)), 4)
        rep = {"year": y, "union_got": got, "alive": len(alive), "coverage_union": cov_u,
               "gate": "PASS" if cov_u >= 0.85 else "GAP_REMAINS",
               "sina_only": round(sum(1 for c in alive if sm["files"].get(c, {}).get("rows", 0) > 0) / max(1, len(alive)), 4),
               "bst_added": sum(1 for c in alive if mans[y][0]["files"].get(c, {}).get("rows", 0) > 0)}
        tmp2 = os.path.join(ROOT, "QC", f"coverage_union_{y}.json.tmp")
        json.dump(rep, open(tmp2, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp2, os.path.join(ROOT, "QC", f"coverage_union_{y}.json"))
        print(y, rep, flush=True)
    bs.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
