# -*- coding: utf-8 -*-
"""R33b: 决策级Coverage_PIT——对F2实际买卖/Gate决策(code,日)统计行业信息'当时可知'比例.
映射: index_component_sw全二级→code→(industry, in_date); 判定: T>=in_date→PARTIAL可知(当前成分口径, 无out_date); T<in_date→UNKNOWN."""
import json
import sys

import akshare as ak
import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")

# 1) 全二级成分→映射表
info = ak.sw_index_second_info()
nc, cc = [c for c in info.columns if "名称" in c][0], [c for c in info.columns if "代码" in c][0]
mapping = {}
for _, rr in info.iterrows():
    code = str(rr[cc]).replace(".SI", "")
    try:
        df = ak.index_component_sw(symbol=code)
        if df is None or len(df) == 0 or "计入日期" not in df.columns:
            continue
        for _, r2 in df.iterrows():
            sc = str(r2.get("证券代码", "")).zfill(6)
            d = str(r2.get("计入日期", ""))
            if sc and d[:4].isdigit():
                mapping[sc] = {"industry": rr[nc], "in_date": d, "industry_code": code}
    except Exception:
        continue
print("map_n:", len(mapping))
json.dump(mapping, open(r"E:\mystockagent\output\ashare\industry_member_map.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 2) F2决策集
f2 = json.load(open(r"E:\mystockagent\output\ashare\swing_m1_F2_realloc_full.json", encoding="utf-8"))
dec = {"gate": set(), "buy": set(), "sell": set()}
for e in f2["gate_log"]:
    dec["gate"].add((e["code"], e["signal_day"]))
for t in f2["trade_log"]:
    if t.get("reason") == "FILLED":
        dec["buy" if t["action"] == "BUY" else "sell"].add((t["code"], t["exec_day"]))

# 3) Coverage_PIT(决策级)
cov = {}
for kind, s in dec.items():
    n = len(s)
    kn = un = 0
    for c, d in s:
        m = mapping.get(c)
        if m and pd.Timestamp(m["in_date"]) <= pd.Timestamp(d):
            kn += 1  # PARTIAL: 当前成分口径可知(无out_date, 版本歧义仍在)
        else:
            un += 1  # UNKNOWN: T<in_date或不在当前成分(历史退出成员不可见)
    cov[kind] = {"n_decisions": n, "known_partial": kn, "unknown": un, "coverage_pit": round(kn / max(1, n), 4)}
print("coverage:", json.dumps(cov, ensure_ascii=False))
out = {"term_fix": "non_switchdate_membership_rate(非行业变更率——含新上市首纳/成分调整/分类修订/回填, 未区分事件类型)",
       "decision_coverage": cov, "mapping_n": len(mapping),
       "status_rule": {"VERIFIED": "可追溯历史来源+版本+有效时间明确且无冲突", "PARTIAL": "有in_date但无out_date/版本不完整(本次known即此级)", "CONFLICT": "多源同日不同归属", "UNKNOWN": "无法确定T日有效归属"},
       "verdict": "决策级Coverage_PIT见上; known=PARTIAL级(当前成分+in_date), 严格VERIFIED需Tushare/官方档案",
       "limitation": "映射=2026当前成分快照; 历史退出成员整体不可见(转型股尤其周期股可能系统性缺失)"}
json.dump(out, open(r"E:\mystockagent\output\ashare\industry_pit_coverage.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("COV-OK")
