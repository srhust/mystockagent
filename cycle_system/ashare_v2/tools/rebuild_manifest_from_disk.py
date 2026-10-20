# -*- coding: utf-8 -*-
"""从已落盘csv重建year manifest(续传保全孤儿进程遗留成果; 校验不过不录入)。"""
import csv
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
COLS = "date,open,high,low,close,preclose,volume,amount,turn,tradestatus,isST"

for y in [2000, 2001, 2002, 2003]:
    out = os.path.join(ROOT, "daily", str(y))
    if not os.path.isdir(out):
        continue
    man = {"year": y, "files": {}, "generated": "REBUILT_FROM_DISK", "rebuild_note":
           "孤儿进程中断后按落盘文件重建续传记录"}
    for fn in os.listdir(out):
        if not fn.endswith(".csv"):
            continue
        p = os.path.join(out, fn)
        with open(p, encoding="utf-8") as f:
            first_line = f.readline().strip()
            if first_line != COLS:
                continue
            rows = list(csv.reader(f))
        if len(rows) < 2:
            continue
        if not all(len(r) == len(COLS.split(",")) for r in rows):   # 全行核列(孤儿kill可能留下半行)
            continue
        first, last = rows[0][0], rows[-1][0]
        if not (f"{y}-01-01" <= first <= last <= f"{y}-12-31"):
            continue
        h = hashlib.sha256(open(p, "rb").read()).hexdigest()
        man["files"][fn[:-4]] = {"rows": len(rows), "sha256": h,
                                 "range_first": first, "range_last": last}
    tmp = os.path.join(out, "manifest.json.tmp")
    json.dump(man, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    os.replace(tmp, os.path.join(out, "manifest.json"))
    print(y, "rebuilt files:", len(man["files"]))
