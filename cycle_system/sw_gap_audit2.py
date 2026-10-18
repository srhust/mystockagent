# -*- coding: utf-8 -*-
"""R44: 缺口审计v2——对照指数有效性前置筛选(2022年实有行情), 8缺失日全查, 五档状态判定."""
import json
import sys

import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m
import akshare as ak

missing = ["2022-03-03", "2022-03-23", "2022-03-31", "2022-04-14", "2022-05-05", "2022-05-06", "2022-05-09", "2022-05-10"]
panel = m.load_panel()
dts = set(pd.DatetimeIndex(sorted(panel["date"].unique())))
# 候选对照(2021版存续/新设混合), 先验有效性: 2022-02与2022-06附近有行情
cands, valid = {}, []
for code in ("801033", "801040", "801950", "801780", "801880"):
    try:
        h = ak.index_hist_sw(symbol=code, period="day")
        h["日期"] = pd.to_datetime(h["日期"])
        s = set(h["日期"])
        ok = any(pd.Timestamp("2022-02-01") <= x <= pd.Timestamp("2022-02-28") for x in s) and any(pd.Timestamp("2022-06-01") <= x <= pd.Timestamp("2022-06-30") for x in s)
        cands[code] = {"rows_2022": int(((h["日期"] >= "2022-01-01") & (h["日期"] <= "2022-12-31")).sum()), "valid_2022": ok}
        if ok:
            valid.append((code, s))
    except Exception as ex:
        cands[code] = {"err": type(ex).__name__}
print("valid_refs:", [c for c, _ in valid], json.dumps(cands))

out = []
for d in missing:
    dt = pd.Timestamp(d)
    open_m = dt in dts
    have = {c: (dt in s) for c, s in valid}
    n_have = sum(have.values())
    if not open_m:
        v = "UNKNOWN(本地开市口径)"
    elif valid and n_have == len(valid):
        v = "SUSPECTED_SOURCE_GAP"  # 多个有效指数同日有, 仅801038缺(不直接确认)
    elif n_have == 0:
        v = "SHARED_MISSING_UNRESOLVED"
    else:
        v = "UNKNOWN"
    out.append({"date": d, "market_open_local": open_m, "refs_have": have, "verdict": v})
res = {"candidate_refs": cands, "valid_refs": [c for c, _ in valid], "days": out,
       "summary": {k: sum(1 for x in out if x["verdict"].startswith(k.split("(")[0])) for k in ("SUSPECTED_SOURCE_GAP", "SHARED_MISSING_UNRESOLVED", "UNKNOWN")},
       "note": "SUSPECTED≠CONFIRMED(他人有行情不证明801038必发布); 独立来源未接入"}
json.dump(res, open(r"E:\mystockagent\output\ashare\sw801038_gap_audit_v2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(json.dumps(res["summary"]), json.dumps([{k: x[k] for k in ("date", "verdict")} for x in out]))
print("R44-OK")
