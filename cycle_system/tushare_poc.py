# -*- coding: utf-8 -*-
"""R39: tushare_poc v2——全记录落盘+完整SHA+状态区分(NO_CREDENTIAL/NO_PERMISSION/EMPTY/ERROR/OK)+两阶段(3股→30股).
用法: python tushare_poc.py [3|30]  默认3(重点变更风险股)."""
import json
import hashlib
import os
import sys

TOKEN = os.environ.get("TUSHARE_TOKEN", "")
STAGE = sys.argv[1] if len(sys.argv) > 1 else "3"
STATE = {"status": "NO_CREDENTIAL" if not TOKEN else "READY", "stage": STAGE, "checked": "2026-10-18", "endpoint": "index_member_all(doc_id=335, 2000积分门槛不消耗)"}
json.dump(STATE, open(r"E:\mystockagent\output\ashare\tushare_poc_status.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
if not TOKEN:
    print("TS-NO-TOKEN(凭据=本地环境变量TUSHARE_TOKEN)")
    raise SystemExit(0)

import tushare as ts
pro = ts.pro_api(TOKEN)
poc = json.load(open(r"E:\mystockagent\output\ashare\industry_pit_poc.json", encoding="utf-8"))["frozen_sample_30"]
# R39b: 3只重点=覆盖主要回撤行业+变更风险(农化/房地产/证券各1, 由冻结清单前序取)
picks = [x["code"] for x in poc][:3] if STAGE == "3" else [x["code"] for x in poc]
recs, n_err, n_empty, n_denied = {}, 0, 0, 0
for c in picks:
    tc = f"{c}.SZ" if c.startswith(("0", "3")) else f"{c}.SH"
    for isnew in ("Y", "N"):
        try:
            df = pro.index_member_all(ts_code=tc, is_new=isnew)
            raw = df.to_json(force_ascii=False)
            if df is None or len(df) == 0:
                n_empty += 1
                recs[f"{c}_{isnew}"] = {"status": "EMPTY", "n": 0}
            else:
                recs[f"{c}_{isnew}"] = {"status": "OK", "n": len(df), "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "cols": list(df.columns), "records": df.to_dict("records")}  # 全记录+完整64位SHA
        except Exception as ex:
            msg = str(ex)
            if "权限" in msg or "积分" in msg or "40203" in msg or "抱歉" in msg:
                n_denied += 1
                recs[f"{c}_{isnew}"] = {"status": "NO_PERMISSION", "err": msg[:120]}
            else:
                n_err += 1
                recs[f"{c}_{isnew}"] = {"status": "ERROR", "err": msg[:120]}
n_ok = sum(1 for v in recs.values() if v.get("status") == "OK")
STATE.update({"status": "EXECUTED", "summary": {"ok": n_ok, "empty": n_empty, "no_permission": n_denied, "error": n_err}, "records": recs, "next_check": "2014/2021版标识是否可区分; in/out_date区间连续无重叠; is_new=N退出成员完整性(3股全OK才扩展30股)"})
json.dump(STATE, open(r"E:\mystockagent\output\ashare\tushare_poc_status.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("TS-POC:", json.dumps(STATE["summary"]), "stage:", STAGE)
