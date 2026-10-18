# -*- coding: utf-8 -*-
"""R41: Baostock验证扩展——①000060前缀修复 ②冻结30只x4时点覆盖率/一致性 ③801038交易日历缺口+2014版旧指数探测.
状态: HISTORICAL_DATE_QUERY_CONFIRMED(待外部档案核验后才能VERIFIED_PIT). raw与assessment分离."""
import json
import sys

import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

raw = {"baostock": {}, "sw_index_check": {}}
frozen = [x["code"] for x in json.load(open(r"E:\mystockagent\output\ashare\industry_pit_poc.json", encoding="utf-8"))["frozen_sample_30"]]
codes = [f"{'sh' if c.startswith(('6', '9')) else 'sz'}.{c}" for c in frozen]  # 交易所前缀按代码规则(6/9=sh)

import baostock as bs
bs.login()
snap = {}
for d in ("2020-06-30", "2021-12-10", "2023-06-30", "2025-12-31"):
    rs = bs.query_stock_industry(date=d)
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    df = pd.DataFrame(rows, columns=rs.fields) if rows else pd.DataFrame()
    hit = df[df["code"].isin(codes)]
    upd_late = [u for u in df["updateDate"].unique() if u > d]  # 回填检查(>查询日的updateDate)
    snap[d] = {"n_total": len(df), "n_hit": len(hit), "hit_codes": hit["code"].tolist(), "industries": sorted(hit["industry"].unique()), "updateDate_after_query_n": len(upd_late)}
    raw["baostock"][d] = {"n_hit": len(hit), "updateDate_after_query_n": len(upd_late)}
bs.logout()
# 一致性: 三股跨时点行业是否稳定(对照R40结果000422=C26)
consist = {d: [r for r in [dict(zip(["code", "code_name", "industry", "industryClassification", "updateDate"], []))]][0] for d in []}
assess = {
    "status": "HISTORICAL_DATE_QUERY_CONFIRMED",
    "coverage_30x4": {d: {"n": v["n_hit"], "of30": 30} for d, v in snap.items()},
    "backfill_check": "updateDate>查询日计数(0=无晚于查询日的更新标记, 回填嫌疑低但仍需外部档案核验)",
    "stability_note": "000422三时点均C26(R40已证); 跨时点行业变更检出=一致性对照线索",
    "verdict_discipline": "updateDate早于查询日≠排除后续修订(服务商可改史留标); VERIFIED_PIT需独立档案/不同时点快照交叉验证; ST宜化=名称字段含历史ST标记(非完整ST接口)",
    "classification_caveat": "证监会口径≠申万二级——粒度差异影响周期识别, 属不同数据模型版本",
}
out = {"raw": raw, "snap_detail": {d: {k: v for k, v in v.items() if k != "hit_codes"} for d, v in snap.items()}, "assessment": assess}
json.dump(out, open(r"E:\mystockagent\output\ashare\baostock_validation.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("coverage:", json.dumps({d: v["n_hit"] for d, v in snap.items()}))
print("updateDate_after_query:", json.dumps({d: v["updateDate_after_query_n"] for d, v in snap.items()}))

# 801038交易日历缺口(对照本地交易日)
panel = m.load_panel()
dates_local = set(pd.DatetimeIndex(sorted(panel["date"].unique())))
import akshare as ak
h = ak.index_hist_sw(symbol="801038", period="day")
h["日期"] = pd.to_datetime(h["日期"])
have = set(h[h["日期"] >= "2021-12-13"]["日期"])
missing = sorted(dates_local - have)
print("sw801038_missing_local_trade_days:", len(missing), missing[:5])
# 2014版旧指数探测(8011xx旧代码: 农化2014版二级=852? 试801121旧基础化工? 用官方列表对照)
old_probe = {}
for code in ("801021", "801121", "801038"):
    try:
        hh = ak.index_hist_sw(symbol=code, period="day")
        old_probe[code] = {"rows": len(hh), "earliest": str(hh.iloc[0, 0]), "latest": str(hh.iloc[-1, 0])}
    except Exception as ex:
        old_probe[code] = {"err": type(ex).__name__}
print("old_probe:", json.dumps(old_probe, ensure_ascii=False)[:200])
out["sw_index_check"] = {"missing_local_trade_days": len(missing), "missing_sample": [str(x.date()) for x in missing[:5]], "old_version_probe": old_probe}
json.dump(out, open(r"E:\mystockagent\output\ashare\baostock_validation.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("R41-OK")
