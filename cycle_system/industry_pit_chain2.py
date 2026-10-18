# -*- coding: utf-8 -*-
"""R36b: 行业指数逐日依赖覆盖(每行业信号日: 成分中PARTIAL/PRE_TAX/UNKNOWN数) + 候选过滤前审计覆盖."""
import json
import sys

from collections import Counter, defaultdict

import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

mapping = json.load(open(r"E:\mystockagent\output\ashare\industry_member_map_v2.json", encoding="utf-8"))["mapping"]
f2 = json.load(open(r"E:\mystockagent\output\ashare\swing_m1_F2_realloc_full.json", encoding="utf-8"))
panel = m.load_panel()
cons = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()  # CURRENT_SNAPSHOT

def st(c, d):
    recs = mapping.get(c)
    if not recs:
        return "SOURCE_MISSING"
    T = pd.Timestamp(d)
    if T < pd.Timestamp("2021-12-13"):
        return "PARTIAL" if any(pd.Timestamp(r["in_date"]) <= T for r in recs) else "PRE_TAXONOMY_VERSION"
    return "PARTIAL" if any(pd.Timestamp(r["in_date"]) <= T for r in recs) else "NOT_IN_CURRENT_MEMBERSHIP"

# 1) 逐日行业指数依赖(按季度抽样日+全行业)
idx_days = [str(x.date()) for x in pd.date_range("2020-04-30", "2026-09-30", freq="QE")]
sec_by_day = {}
for d in idx_days:
    agg = defaultdict(Counter)
    for c, sec in cons.items():
        agg[sec][st(c, d)] += 1
    sec_by_day[d] = {k: dict(v) for k, v in agg.items()}
per = {}
for d, aggs in sec_by_day.items():
    b = "2020-2021" if d[:4] <= "2021" else ("2022-2023" if d[:4] <= "2023" else "2024-2026")
    tot = per.setdefault(b, Counter())
    for sec, cnt in aggs.items():
        tot.update(cnt)
per_out = {b: {k: v for k, v in c.items()} for b, c in per.items()}

# 2) 行业过滤前候选覆盖(新盲区)
cand = f2["cand_log"]
ind_rej = Counter()
for x in cand:
    if x["state"] == "REJ_IND_FILTER":
        ind_rej[st(x["code"], x["day"])] += 1
n_ir = sum(ind_rej.values())
out = {"index_daily_by_period": per_out, "index_note": "成分=当前快照分组(CURRENT_SNAPSHOT); PRE_TAXONOMY_VERSION=版本切换前不可知; 无out_date仍是结构性缺口",
       "ind_filter_pre_rej": {"n": n_ir, "states": dict(ind_rej), "partial_rate": round(ind_rej.get("PARTIAL", 0) / max(1, n_ir), 4),
                              "note": "被行业good过滤排除的193455候选(此前完全不可见)——其中版本切换前不可知占比即该阶段筛选的穿越暴露"},
       "cand_log_v2_semantics": "ELIGIBLE=通过行业过滤+可交易(先于MA20/pending后筛); 与v1(Top5)口径不同",
       "verdict": "依赖链最后一盲区(行业过滤前)已量化; 工程审计到此关闭, 后续=历史成员数据源验证(Tushare/官方档案)"}
json.dump(out, open(r"E:\mystockagent\output\ashare\industry_pit_chain_v2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("ind_filter_pre_rej:", json.dumps(out["ind_filter_pre_rej"]["states"]), "partial_rate:", out["ind_filter_pre_rej"]["partial_rate"])
print("by_period:", json.dumps(per_out, ensure_ascii=False)[:260])
print("R36B-OK")
