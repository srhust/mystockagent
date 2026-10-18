# -*- coding: utf-8 -*-
"""R40: 免费数据源有界POC——①Baostock历史行业(date参数, 2020/2021/2023, updateDate回填检查)
②AKShare index_hist_sw官方历史指数完整性 ③聚宽jqdatasdk可得性(无凭据→UNKNOWN). 只读, 不碰冻结策略."""
import json
import sys

out = {"baostock": {}, "akshare_sw_hist": {}, "joinquant": {}, "note": "只读POC; 冻结策略不修改; B目标=历史可用信息(可换分类体系), A目标=申万二级恢复(更难)"}
# 1) Baostock
try:
    import baostock as bs
    lg = bs.login()
    codes = ["sz.000422", "sz.000776", "sh.000060"]
    for d in ("2020-06-30", "2021-12-10", "2023-06-30"):
        rs = bs.query_stock_industry(date=d)
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        import pandas as pd
        df = pd.DataFrame(rows, columns=rs.fields) if rows else pd.DataFrame()
        hit = df[df["code"].isin(codes)] if len(df) else df
        upd = sorted(df["updateDate"].unique())[:3] if len(df) else []
        out["baostock"][d] = {"n_total": len(df), "fields": list(rs.fields), "pilot3": hit.to_dict("records"), "updateDate_sample": upd, "backfill_check": "updateDate若=晚于查询日的统一值→回填修订嫌疑"}
    bs.logout()
    out["baostock"]["status"] = "EXECUTED"
except Exception as ex:
    out["baostock"]["status"] = f"UNAVAILABLE: {type(ex).__name__}: {str(ex)[:80]}"

# 2) AKShare 申万历史指数行情
try:
    import akshare as ak
    h = ak.index_hist_sw(symbol="801038", period="day")
    h20 = h[(h["日期"] >= "2020-01-01") & (h["日期"] <= "2026-10-17")] if "日期" in h.columns else h
    out["akshare_sw_hist"] = {"n_rows_total": len(h), "n_rows_2020_2026": len(h20), "cols": list(h.columns)[:6], "earliest": str(h.iloc[0, 0]), "latest": str(h.iloc[-1, 0]), "gaps_note": "交易日连续性待后续核对"}
except Exception as ex:
    out["akshare_sw_hist"] = {"status": f"UNAVAILABLE: {type(ex).__name__}: {str(ex)[:80]}"}

# 3) 聚宽
try:
    import jqdatasdk
    out["joinquant"] = {"status": "SDK_INSTALLED_NO_AUTH(需账号; get_industry(sw_l2, date)待权限验证)"}
except ImportError:
    out["joinquant"] = {"status": "SDK_NOT_INSTALLED(免费账号路径待用户确认)"}

json.dump(out, open(r"E:\mystockagent\output\ashare\free_source_poc.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("bs:", out["baostock"].get("status"), {k: v.get("n_total") for k, v in out["baostock"].items() if isinstance(v, dict) and "n_total" in v})
print("sw_hist:", out["akshare_sw_hist"].get("n_rows_2020_2026", out["akshare_sw_hist"]))
print("jq:", out["joinquant"]["status"][:50])
print("R40-OK")
