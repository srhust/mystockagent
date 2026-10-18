# -*- coding: utf-8 -*-
"""R42: ①撤回801021↔801038衔接(801021=煤炭开采, 农化制品为2021新设) ②120条逐股记录落盘+SHA
③新旧指数各自有效区间完整性(缺口/重复/OHLC缺失). 不拼接不同行业指数."""
import json
import hashlib
import sys

import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

frozen = [x["code"] for x in json.load(open(r"E:\mystockagent\output\ashare\industry_pit_poc.json", encoding="utf-8"))["frozen_sample_30"]]
codes = [f"{'sh' if c.startswith(('6', '9')) else 'sz'}.{c}" for c in frozen]
recs = []
import baostock as bs
bs.login()
for d in ("2020-06-30", "2021-12-10", "2023-06-30", "2025-12-31"):
    rs = bs.query_stock_industry(date=d)
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    df = pd.DataFrame(rows, columns=rs.fields) if rows else pd.DataFrame()
    for _, r in df[df["code"].isin(codes)].iterrows():
        recs.append({"query_date": d, "stock_code": r["code"], "industry": r["industry"], "industryClassification": r["industryClassification"], "updateDate": r["updateDate"], "source": "baostock.query_stock_industry"})
bs.logout()
payload = {"records_n": len(recs), "records": recs, "sha256_records": hashlib.sha256(json.dumps(recs, ensure_ascii=False, sort_keys=True).encode()).hexdigest(), "field_note": "updateDate_after_query=晚于查询日的不同更新日期数(非记录数, R41字段名修正)"}
json.dump(payload, open(r"E:\mystockagent\output\ashare\baostock_120_records.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("records:", len(recs))

# 各自区间完整性
panel = m.load_panel()
dates_local = sorted(pd.DatetimeIndex(sorted(panel["date"].unique())))
import akshare as ak
res = {}
for code, start, end in (("801038", "2021-12-13", "2026-10-16"), ("801021", None, "2021-12-10")):
    h = ak.index_hist_sw(symbol=code, period="day")
    h["日期"] = pd.to_datetime(h["日期"])
    h = h.sort_values("日期")
    if start:
        seg = h[(h["日期"] >= start) & (h["日期"] <= end)]
        local = [d for d in dates_local if pd.Timestamp(start) <= d <= pd.Timestamp(end)]
    else:
        seg = h[h["日期"] <= end]
        local = [d for d in dates_local if d <= pd.Timestamp(end)]
    missing = [str(x.date()) for x in sorted(set(local) - set(seg["日期"]))]
    dup = int(seg["日期"].duplicated().sum())
    ohlc_na = int(seg[["开盘", "最高", "最低", "收盘"]].isna().sum().sum()) if all(c in seg.columns for c in ("开盘", "最高", "最低", "收盘")) else -1
    res[code] = {"n_rows": len(seg), "n_local_trade_days": len(local), "missing": len(missing), "missing_sample": missing[:5], "dup_days": dup, "ohlc_na": ohlc_na}
print(json.dumps(res, ensure_ascii=False))

# 映射撤回+旧版线索
out = {"retraction": "R41'801021↔801038无缝衔接'撤回: 801021=煤炭开采(2021-12-10止), 801038=农化制品(2021新设)——不同行业, 日期相邻≠对应; 农化在2014版属化学原料(801033)/化学制品(801034)线索, 精确映射需官方分类变更关系",
       "baostock_records": payload, "index_integrity": res,
       "status": "HISTORICAL_DATE_QUERY_CONFIRMED(不升级VERIFIED_PIT); 不拼接不同行业指数"}
json.dump(out, open(r"E:\mystockagent\output\ashare\industry_source_evidence_v2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("R42-OK")
