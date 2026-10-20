# -*- coding: utf-8 -*-
"""B线探针: cninfo p_stock2110 行业归属历史的可回溯深度+退市券覆盖实测(15样本)。"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                # noqa: E402
import akshare as ak                                                           # noqa: E402

SAMPLES = [
    ("600000", "2000年在市"), ("600008", "2000年在市"), ("600009", "2000年在市"),
    ("600010", "2000年在市"), ("000001", "2000年在市"), ("000002", "2000年在市"),
    ("000012", "2000年在市"), ("000024", "2000年在市"), ("000063", "2000年在市"),
    ("600001", "退市2009"), ("600002", "退市2006"), ("600065", "退市2007"),
    ("600003", "退市2010"), ("600068", "退市2021"), ("60005", "无效码对照"),
]
out = {"source": "akshare.stock_industry_change_cninfo(p_stock2110)", "probed": [], "summary": {}}
for code, note in SAMPLES:
    rec = {"code": code, "note": note}
    try:
        df = ak.stock_industry_change_cninfo(symbol=code, start_date="20000101", end_date="20261020")
        rec["rows"] = len(df)
        if len(df):
            rec["first_change"] = str(df["变更日期"].min())
            rec["standards"] = sorted(set(df["分类标准"]))
            rec["sw_old_any"] = any("申银万国" in s for s in rec["standards"])
    except Exception as e:
        rec["error"] = f"{type(e).__name__}:{str(e)[:80]}"
    out["probed"].append(rec)
    time.sleep(0.5)
p_list = [r for r in out["probed"] if r.get("rows", 0) > 0]
out["summary"] = {
    "hit": len(p_list), "of": len(SAMPLES),
    "earliest_back": min((r["first_change"] for r in p_list), default=None),
    "delisted_coverage": sum(1 for r in out["probed"] if "退市" in r["note"] and r.get("rows", 0) > 0),
    "delisted_total": sum(1 for r in out["probed"] if "退市" in r["note"]),
    "sw_standard_seen": sum(1 for r in p_list if r.get("sw_old_any")),
}
dest = os.path.join(C.DATA_DIR, "ashare_history_2000", "QC", "B_pit_probe_p2110.json")
tmp = dest + ".tmp"
json.dump(out, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
os.replace(tmp, dest)
print(json.dumps(out["summary"], ensure_ascii=False, indent=1))
for r in out["probed"][:16]:
    print(r.get("code"), r.get("rows", r.get("error", "")[:40]), r.get("first_change", ""), r.get("sw_old_any", ""))
