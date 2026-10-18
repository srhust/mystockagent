# -*- coding: utf-8 -*-
"""R33a: AKShare index_component_sw PIT可得性决定性验证——全二级成分计入日期分布.
若日期多样=调整可追溯(部分PIT可行); 若仅2021-12-13单值=仅版本切换无调整史(仍不足)."""
import json
import sys

import akshare as ak
import pandas as pd

info = ak.sw_index_second_info()
nc, cc = [c for c in info.columns if "名称" in c][0], [c for c in info.columns if "代码" in c][0]
rows, err = [], 0
for _, rr in info.iterrows():
    code = str(rr[cc]).replace(".SI", "")
    try:
        df = ak.index_component_sw(symbol=code)
        if df is None or len(df) == 0:
            rows.append({"code": code, "name": rr[nc], "n": 0, "distinct_dates": 0, "non_switchdate_n": 0})
            continue
        dt = df.get("计入日期")
        nn = dt.dropna().astype(str)
        nn = nn[nn.str.match(r"\d{4}-\d{2}-\d{2}")]
        cnt = (nn != "2021-12-13").sum()
        rows.append({"code": code, "name": rr[nc], "n": len(df), "date_valid": len(nn), "distinct_dates": int(nn.nunique()), "non_switchdate_n": int(cnt), "non_switchdate_sample": sorted(nn[nn != "2021-12-13"].unique())[:5]})
    except Exception:
        err += 1
        rows.append({"code": code, "name": rr[nc], "n": -1})
tot = sum(r["n"] for r in rows if r["n"] > 0)
nonsw = sum(r.get("non_switchdate_n", 0) for r in rows)
out = {"survey": rows, "summary": {"n_industries": len(rows), "err": err, "total_cons": tot, "cons_with_non_switchdate": nonsw, "pct_non_switchdate": round(nonsw / max(1, tot), 4)}, "verdict": None}
if nonsw / max(1, tot) > 0.05:
    out["verdict"] = "PARTIAL_PIT: 计入日期含版本切换后调整记录(>5%非切换日)——纳入方向可追溯, 但仍无out_date(剔除不可见)"
else:
    out["verdict"] = "SWITCH_DATE_ONLY: 计入日期几乎全部=2021-12-13(版本切换日)——后续行业调整未记录, PIT重建仍不足"
json.dump(out, open(r"E:\mystockagent\output\ashare\industry_pit_sw_survey.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(json.dumps(out["summary"], ensure_ascii=False))
print("VERDICT:", out["verdict"])
print("SURVEY-OK")
