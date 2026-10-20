# -*- coding: utf-8 -*-
"""p2110_pit_coverage — 指标7(行业PIT覆盖)分析器: 读industry_p2110全量算逐券最早可回溯日期.

输出 QC/p2110_pit_coverage.json: 按年统计存续券中"存在 变更日期<=Y-01-01 记录"的比例
(=区间头覆盖), 头前按纪律=UNKNOWN。分标准(申万旧/新/巨潮/中证/协会)细账。只读。
"""
from __future__ import annotations
import csv, glob, json, os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                        # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                    # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")

rows = []
man = json.load(open(os.path.join(ROOT, "industry_p2110", "manifest.json"), encoding="utf-8"))
n_csv = 0
earliest = {}
per_std = {}
for p in glob.glob(os.path.join(ROOT, "industry_p2110", "*.csv")):
    code = os.path.basename(p)[:-4]
    n_csv += 1
    with open(p, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        std_col = next((c for c in rd.fieldnames if "标准" in c or "行业分类" in c), None)
        date_col = next((c for c in rd.fieldnames if "变更日期" in c), None)
        if not date_col:
            continue
        ds = []
        for r in rd:
            d = (r.get(date_col) or "")[:10]
            if d:
                ds.append(d)
                if std_col:
                    per_std.setdefault(r[std_col], set()).add(code)
        if ds:
            earliest[code] = min(ds)

res = {"csv_files": n_csv, "with_records": len(earliest), "empty": man["stats"].get("empty") if "stats" in man else sum(1 for v in man["files"].values() if v.get("rows", 0) == 0)}
for y in [2000, 2001, 2002, 2003]:
    alive = [l.strip() for l in open(os.path.join(ROOT, "universe", str(y), "alive.csv"), encoding="utf-8") if l.strip()]
    cov = sum(1 for c in alive if earliest.get(c, "9999") <= f"{y}-01-01")
    res[f"Y{y}_存续"] = len(alive)
    res[f"Y{y}_行业PIT覆盖(区间头<=年初)"] = round(cov / max(1, len(alive)), 4)
res["earliest_year_dist"] = {}
for c, d in earliest.items():
    res["earliest_year_dist"][d[:4]] = res["earliest_year_dist"].get(d[:4], 0) + 1
res["per_std_codes"] = {k: len(v) for k, v in per_std.items()}
tmp = os.path.join(ROOT, "QC", "p2110_pit_coverage.json.tmp")
json.dump(res, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
os.replace(tmp, os.path.join(ROOT, "QC", "p2110_pit_coverage.json"))
print(json.dumps({k: v for k, v in res.items() if not isinstance(v, dict)}, ensure_ascii=False, indent=1))
