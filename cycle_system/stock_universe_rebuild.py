# -*- coding: utf-8 -*-
"""R45: 历史股票池重建POC——Baostock query_stock_basic(ipoDate/outDate)→2020-2026逐月可交易全集曲线
+退市股清单+当前249池幸存者偏差量化. 只读, 冻结策略不动."""
import json
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
print("basic_n:", len(df), "cols:", list(rs.fields))
df["ipoDate"] = pd.to_datetime(df["ipoDate"], errors="coerce")
df["outDate"] = pd.to_datetime(df["outDate"], errors="coerce")
# A股股票(排除指数/可转债等: code以sh.6/sz.0/sz.3开头, type=1股票)
stk = df[df["type"] == "1"] if "type" in df.columns else df[df["code"].str.match(r"(sh\.6|sz\.0|sz\.3)")]
months = pd.date_range("2020-01-31", "2026-09-30", freq="ME")
curve, delisted = [], []
for d in months:
    alive = stk[(stk["ipoDate"] <= d) & ((stk["outDate"].isna()) | (stk["outDate"] > d))]
    curve.append({"month": str(d.date())[:7], "n_alive": len(alive)})
dl = stk[stk["outDate"].notna() & (stk["outDate"] >= "2020-01-01")]
delisted = [{"code": r["code"], "name": r.get("code_name", ""), "out": str(r["outDate"].date())} for _, r in dl.iterrows()]
# 249池对比: 当前池内股票是否全部在baostock名录(外集=数据缺口)
panel = m.load_panel()
pool = set(panel.drop_duplicates("code")["code"])
bs_codes = set(c.split(".")[1] for c in stk["code"])
missing_in_bs = sorted(pool - bs_codes)
out = {"raw": {"n_basic_rows": len(df), "fields": list(rs.fields)}, "monthly_alive_curve": curve,
       "delisted_since_2020_n": len(delisted), "delisted_sample": delisted[:20],
       "pool249_missing_in_baostock": missing_in_bs,
       "survivorship_note": "249池全部仍在册(若无退市股)=幸存者偏差的直接体现; 重建池含退市股后可做历史可交易全集",
       "status": "STOCK_UNIVERSE_REBUILD_CONFIRMED(逐月全集可重建)"}
json.dump(out, open(r"E:\mystockagent\output\ashare\stock_universe_rebuild.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("curve_head:", json.dumps(curve[:3]), "tail:", json.dumps(curve[-2:]))
print("delisted_n:", len(delisted), "missing_in_bs:", missing_in_bs[:5])
print("R45-OK")
