# -*- coding: utf-8 -*-
"""R47: 覆盖差异报告——①退市时间边界三拆分 ②四代表月x六项(L1上市存续覆盖/L2证监会代理行业候选/INDUSTRY_UNKNOWN)."""
import json
import hashlib
import sys

import baostock as bs
import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

ASOF = "2026-10-18"
lg = bs.login()
rs = bs.query_stock_basic()
rows = []
while rs.error_code == "0" and rs.next():
    rows.append(rs.get_row_data())
df = pd.DataFrame(rows, columns=rs.fields)
df["ipoDate"] = pd.to_datetime(df["ipoDate"], errors="coerce")
df["outDate"] = pd.to_datetime(df["outDate"], errors="coerce")
stk = df[df["type"] == "1"]
# 1) 时间边界三拆分
dl = stk[stk["outDate"].notna() & (stk["outDate"] >= "2020-01-01")]
asof = dl[dl["outDate"] <= ASOF]
fut = dl[dl["outDate"] > ASOF]
bounds = {"DELISTED_EFFECTIVE_TO_ASOF": len(asof), "FUTURE_OUTDATE_IN_SOURCE": [{"code": r["code"], "out": str(r["outDate"].date())} for _, r in fut.iterrows()], "DELISTED_IN_RESEARCH_WINDOW(2020-01~2026-09)": int((dl["outDate"] <= "2026-09-30").sum())}
# 2) Baostock历史行业(代理周期行业: 证监会C门类细分关键词)
proxy_kw = ("化学", "煤炭", "金属", "黑色", "有色", "房地产", "非金属", "石油")
pool = set(m.load_panel().drop_duplicates("code")["code"])
months = ["2020-06-30", "2022-06-30", "2024-06-30", "2026-06-30"]
rows_by_m = {}
bs.login()
for d in months:
    rs2 = bs.query_stock_industry(date=d)
    rr = []
    while rs2.error_code == "0" and rs2.next():
        rr.append(rs2.get_row_data())
    rows_by_m[d] = pd.DataFrame(rr, columns=rs2.fields) if rr else pd.DataFrame()
bs.logout()
report = []
import shutil
for d in months:
    ind = rows_by_m[d]
    if ind is None or len(ind) == 0:
        continue
    alive = stk[(stk["ipoDate"] <= d) & ((stk["outDate"].isna()) | (stk["outDate"] > d))]
    alive_codes = set(c.split(".")[1] for c in alive["code"])
    pool_alive = pool & alive_codes
    ind["code6"] = ind["code"].str.split(".").str[1]
    cyc = ind[ind["industry"].str.contains("|".join(proxy_kw), na=False)]
    cyc_alive = set(cyc["code6"]) & alive_codes
    report.append({"month": d, "alive_n": len(alive_codes), "pool249_alive": len(pool_alive),
                   "not_in_pool_alive": len(alive_codes - pool),
                   "proxy_cyc_candidates_L2": len(cyc_alive),
                   "proxy_cyc_not_in_pool": len(cyc_alive - pool),
                   "INDUSTRY_UNKNOWN": int(len(alive_codes) - ind["code6"].isin(set(ind["code6"])).sum()) if len(ind) else len(alive_codes)})
out = {"bounds": bounds, "coverage_report_L1_L2": report, "proxy_note": "L2=证监会口径周期代理(关键词匹配), 非申万二级——不得替代L3; L3待申万源",
       "anti_lookahead": "资格仅用当时可知信息(ipo/out/当时行业快照), 不带入'今天知道会退市'",
       "sha": hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()[:16]}
json.dump(out, open(r"E:\mystockagent\output\ashare\coverage_gap_report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(json.dumps(bounds["FUTURE_OUTDATE_IN_SOURCE"]), "asof:", bounds["DELISTED_EFFECTIVE_TO_ASOF"])
print(json.dumps(report, ensure_ascii=False, indent=0)[:400])
print("R47-OK")
