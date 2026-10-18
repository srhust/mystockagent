# -*- coding: utf-8 -*-
"""R46: ①完整227退市名单落盘+SHA ②type=1官方定义核查+前缀分布 ③状态六分表(DELISTED_MARKET_WIDE口径)."""
import json
import hashlib
import sys

import baostock as bs
import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

lg = bs.login()
rs = bs.query_stock_basic()
rows = []
while rs.error_code == "0" and rs.next():
    rows.append(rs.get_row_data())
bs.logout()
df = pd.DataFrame(rows, columns=rs.fields)
df["ipoDate"] = pd.to_datetime(df["ipoDate"], errors="coerce")
df["outDate"] = pd.to_datetime(df["outDate"], errors="coerce")
# type/status全值分布(官方字段口径核查)
type_dist = df["type"].value_counts().to_dict() if "type" in df.columns else {}
status_dist = df["status"].value_counts().to_dict() if "status" in df.columns else {}
stk = df[df["type"] == "1"] if "type" in df.columns else df[df["code"].str.match(r"(sh\.6|sz\.0|sz\.3)")]
pref = stk["code"].str[:5].value_counts().to_dict()
# 全量退市名单(完整落盘)
dl = stk[stk["outDate"].notna() & (stk["outDate"] >= "2020-01-01")].sort_values("outDate")
delisted_full = [{"code": r["code"], "name": r.get("code_name", ""), "ipo": str(r["ipoDate"].date()), "out": str(r["outDate"].date())} for _, r in dl.iterrows()]
recs_sha = hashlib.sha256(json.dumps(delisted_full, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
# 249池行业(快照)×退市股行业关联线索: 退市股中周期行业占比(用证监会行业? 名录无行业——用Baostock行业接口当前+前值不可得, 退市股行业=INDUSTRY_UNKNOWN单独标记)
out = {"type_dist": type_dist, "status_dist": status_dist, "prefix_dist": pref,
       "delisted_full_n": len(delisted_full), "delisted_full": delisted_full, "sha256_delisted": recs_sha,
       "caliber_fix": "DELISTED_MARKET_WIDE=227(全市场退市数, 非MISSED_ELIGIBLE——退市股是否本应入池取决于当时行业/流动性/上市时间/选股条件, 未量化)",
       "alive_meaning": "n_alive=月末上市存续数(非可交易数——停牌/ST/涨跌停/上市初期资格未验证)",
       "status_table": {"历史上市及退市日期查询": "CONFIRMED", "历史上市存续股票池": "RECONSTRUCTABLE", "历史实际可交易股票池": "NOT_VERIFIED", "历史行业归属(证监会)": "HISTORICAL_DATE_QUERY_CONFIRMED", "历史行业归属(申万二级)": "UNKNOWN", "幸存者偏差对收益的影响": "NOT_ESTIMATED"}}
json.dump(out, open(r"E:\mystockagent\output\ashare\stock_universe_full.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("type:", type_dist, "status:", status_dist)
print("delisted_full:", len(delisted_full), "sha:", recs_sha[:12])
print("R46-OK")
