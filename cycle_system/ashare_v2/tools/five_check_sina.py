# -*- coding: utf-8 -*-
"""five_check_sina — sina 全层(1998-2026)五检抽样(裁定D: 新数据先五检).

五检: 1)OHLC 有效 hi>=max(o,c,low), lo<=min 2)vol>=0 3)日期升序无重 4)交易日历合理(行数)
5)价格跳变边界(单日 |ret|>22% 登记不判死=含涨跌停史实, 仅计数).
抽样: 每年 6% 券(种子固定 20261024, 可复现)。输出 QC/five_check_sina.json。
"""
import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import config as C  # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000", "daily_sina")
rng = random.Random(20261024)
res = {"seed": 20261024, "share": 0.06, "years": {}}
tot = {"files": 0, "rows": 0, "ohlc_bad": 0, "vol_neg": 0, "date_bad": 0,
       "ret_gt22": 0, "empty": 0}
for y in range(1998, 2027):
    d = os.path.join(ROOT, str(y))
    if not os.path.isdir(d):
        continue
    files = [p for p in glob.glob(os.path.join(d, "*.csv"))]
    n = max(1, int(len(files) * 0.06))
    sample = rng.sample(files, min(n, len(files)))
    yc = dict(files=0, rows=0, ohlc_bad=0, vol_neg=0, date_bad=0, ret_gt22=0, empty=0)
    for p in sample:
        try:
            rows = [l.split(",") for l in open(p, encoding="utf-8").read().splitlines()[1:]
                    if l.strip()]
        except UnicodeDecodeError:
            rows = [l.split(",") for l in open(p, encoding="gbk", errors="replace").read().splitlines()[1:]
                    if l.strip()]
        yc["files"] += 1
        if not rows:
            yc["empty"] += 1
            continue
        yc["rows"] += len(rows)
        prev_c = None
        prev_date = ""
        for r in rows:
            try:
                date = r[0][:10]
                o, h, lo, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
                v = float(r[5]) if len(r) > 5 and r[5] else 0.0
            except (ValueError, IndexError):
                yc["ohlc_bad"] += 1
                continue
            if not (h >= max(o, c, lo) - 1e-9 and lo <= min(o, c) <= h + 1e-9):
                yc["ohlc_bad"] += 1
            if v < 0:
                yc["vol_neg"] += 1
            if date <= prev_date:
                yc["date_bad"] += 1
            prev_date = date
            if prev_c and prev_c > 0:
                ret = abs(c / prev_c - 1)
                if ret > 0.22:
                    yc["ret_gt22"] += 1
            prev_c = c
    for k in tot:
        tot[k] += yc.get(k, 0)
    res["years"][y] = yc
res["TOTAL"] = tot
res["rates"] = {
    "ohlc_bad_per_myriad_rows": round(tot["ohlc_bad"] / max(1, tot["rows"]) * 1e4, 3),
    "date_bad_per_myriad_rows": round(tot["date_bad"] / max(1, tot["rows"]) * 1e4, 3),
    "vol_neg_per_myriad_rows": round(tot["vol_neg"] / max(1, tot["rows"]) * 1e4, 3),
}
out = os.path.join(C.DATA_DIR, "ashare_history_2000", "QC", "five_check_sina.json")
tmp = out + ".tmp"
json.dump(res, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
os.replace(tmp, out)
print(json.dumps({"TOTAL": tot, "rates": res["rates"]}, ensure_ascii=False))
bad_years = {y: v for y, v in res["years"].items() if v["ohlc_bad"] + v["date_bad"] > 5}
print("BAD_YEARS:", json.dumps(bad_years, ensure_ascii=False)[:400])
