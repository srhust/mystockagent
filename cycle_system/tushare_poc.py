# -*- coding: utf-8 -*-
"""R38: Tushare index_member_all 只读POC(凭据=本地环境变量TUSHARE_TOKEN; 缺失→UNKNOWN不制造数据).
验收: is_new=Y/N双查, 冻结30样本, 原始响应+SHA+查询日期+版本标识留存."""
import json
import hashlib
import os

import pandas as pd

TOKEN = os.environ.get("TUSHARE_TOKEN", "")
STATE = {"status": "NO_CREDENTIAL" if not TOKEN else "READY", "endpoint": "pro.query('index_member_all')", "doc": "tushare.pro/document/2?doc_id=335(2000积分门槛, 权限分级不消耗积分/次数随积分增长)", "checked": "2026-10-18"}
if not TOKEN:
    json.dump(STATE, open(r"E:\mystockagent\output\ashare\tushare_poc_status.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("TS-NO-TOKEN: 环境变量TUSHARE_TOKEN未配置; POC就绪待凭据(用户本地配置, 不经聊天)")
    raise SystemExit(0)

import tushare as ts
pro = ts.pro_api(TOKEN)
frozen = [x["code"] for x in json.load(open(r"E:\mystockagent\output\ashare\industry_pit_poc.json", encoding="utf-8"))["frozen_sample_30"]]
recs = {}
for c in frozen:
    for isnew in ("Y", "N"):
        try:
            df = pro.index_member_all(ts_code=f"{c}.SZ" if c.startswith(("0", "3")) else f"{c}.SH", is_new=isnew)
            raw = df.to_json(force_ascii=False)
            recs[f"{c}_{isnew}"] = {"n": len(df), "sha256_16": hashlib.sha256(raw.encode()).hexdigest()[:16], "cols": list(df.columns), "head": df.head(2).to_dict("records")}
        except Exception as ex:
            recs[f"{c}_{isnew}"] = {"err": f"{type(ex).__name__}: {str(ex)[:80]}"}
STATE.update({"status": "EXECUTED", "records": recs, "note": "待核: 2014/2021版标识/in-out_date区间连续性/退出成员完整性"})
json.dump(STATE, open(r"E:\mystockagent\output\ashare\tushare_poc_status.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("TS-POC-OK", json.dumps({k: v.get("n", v.get("err")) for k, v in list(recs.items())[:4]}))
