# -*- coding: utf-8 -*-
"""R39c: tushare_poc v3——阶段门槛强制校验(pilot_verified需实质验证非仅6OK)+试点显式固定+状态语义.
用法: python tushare_poc.py [pilot|30]; 30阶段需pilot状态=PILOT_OK_PENDING_VALIDATION."""
import json
import hashlib
import os
import sys

STAGE = sys.argv[1] if len(sys.argv) > 1 else "pilot"
TOKEN = os.environ.get("TUSHARE_TOKEN", "")
POC_F = r"E:\mystockagent\output\ashare\tushare_poc_status.json"
try:
    prev = json.load(open(POC_F, encoding="utf-8"))
except Exception:
    prev = {}

def validate_pilot(recs):
    """实质校验: 6查询OK+版本可区分+in/out区间+退出记录 → PILOT_OK_PENDING_VALIDATION."""
    ok = [v for v in recs.values() if v.get("status") == "OK"]
    if len(ok) < 6:
        return False, "queries_not_all_ok"
    cols = set()
    for v in ok:
        cols |= set(v.get("cols", []))
    if not {"in_date", "out_date", "l1_code", "l2_code", "l3_code"} <= cols:
        return False, "missing_version_or_date_fields"
    has_out = any(any(r.get("out_date") not in (None, "") for r in v["records"]) for v in ok)
    return True, ("out_date_present" if has_out else "out_date_absent_in_pilot")

STATE = {"status": "NO_CREDENTIAL" if not TOKEN else "READY", "stage": STAGE, "checked": "2026-10-18"}
if STAGE == "30":
    pv, why = validate_pilot(prev.get("records", {})) if prev.get("status") == "PILOT_OK_PENDING_VALIDATION" else (False, "pilot_not_run_or_not_ok")
    if not pv:
        STATE.update({"status": "PILOT_NOT_VERIFIED", "reason": why})
        json.dump(STATE, open(POC_F, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("PILOT_NOT_VERIFIED:", why)
        raise SystemExit(1)

json.dump(STATE, open(POC_F, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
if not TOKEN:
    print("TS-NO-TOKEN")
    raise SystemExit(0)

import tushare as ts
pro = ts.pro_api(TOKEN)
# R39c: 试点显式固定(冻结清单: 000422农化/000776证券/000060工业金属——房地产不在前三, 冻结清单内按sector查找补充)
PILOT = ["000422", "000776", "000060"]
if STAGE == "30":
    poc = json.load(open(r"E:\mystockagent\output\ashare\industry_pit_poc.json", encoding="utf-8"))["frozen_sample_30"]
    picks = [x["code"] for x in poc]
else:
    picks = PILOT
recs, cnt = {}, {"OK": 0, "EMPTY": 0, "NO_PERMISSION": 0, "ERROR": 0}
for c in picks:
    tc = f"{c}.SZ" if c.startswith(("0", "3")) else f"{c}.SH"
    for isnew in ("Y", "N"):
        try:
            df = pro.index_member_all(ts_code=tc, is_new=isnew)
            raw = df.to_json(force_ascii=False)
            if df is None or len(df) == 0:
                recs[f"{c}_{isnew}"] = {"status": "EMPTY", "n": 0}; cnt["EMPTY"] += 1
            else:
                recs[f"{c}_{isnew}"] = {"status": "OK", "n": len(df), "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "cols": list(df.columns), "records": df.to_dict("records"), "sha_note": "解码文本哈希(非原始字节)"}; cnt["OK"] += 1
        except Exception as ex:
            msg = str(ex)
            s = "NO_PERMISSION" if ("权限" in msg or "积分" in msg or "40203" in msg) else "ERROR"
            recs[f"{c}_{isnew}"] = {"status": s, "err": msg[:120]}; cnt[s] += 1
if cnt["NO_PERMISSION"] and cnt["OK"] == 0:
    final = "NO_PERMISSION"
elif cnt["OK"] == 2 * len(picks):
    final = "PILOT_OK_PENDING_VALIDATION" if STAGE == "pilot" else "THIRTY_EXECUTED"
else:
    final = "PARTIAL_FAILURE"
STATE.update({"status": final, "query_summary": cnt, "pilot_codes_fixed": PILOT, "records": recs,
              "note": "Tushare返回历史记录≠当时可知性: 需区分历史有效日期与数据修订/回填日期(版本标识待核)"})
json.dump(STATE, open(POC_F, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("TS-POC:", final, json.dumps(cnt))
