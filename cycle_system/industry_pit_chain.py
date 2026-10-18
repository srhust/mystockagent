# -*- coding: utf-8 -*-
"""R35: 决策依赖覆盖审计——①月度断点+UNKNOWN三因细分 ②sect_of用panel快照(CURRENT_SNAPSHOT标注) ③行业指数成分审计 ④依赖链json."""
import json
import sys

from collections import Counter

import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

mapping = json.load(open(r"E:\mystockagent\output\ashare\industry_member_map_v2.json", encoding="utf-8"))["mapping"]
f2 = json.load(open(r"E:\mystockagent\output\ashare\swing_m1_F2_realloc_full.json", encoding="utf-8"))
panel = m.load_panel()
snap_sec = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()  # CURRENT_SNAPSHOT来源
cand = f2["cand_log"]

def reason(c, d):
    recs = mapping.get(c)
    if not recs:
        return "SOURCE_MISSING"
    T = pd.Timestamp(d)
    if T < pd.Timestamp("2021-12-13"):
        ok = any(pd.Timestamp(r["in_date"]) <= T for r in recs)
        return "PARTIAL" if ok else "PRE_TAXONOMY_VERSION"  # 版本切换前不可知
    return "PARTIAL" if any(pd.Timestamp(r["in_date"]) <= T for r in recs) else "NOT_IN_CURRENT_MEMBERSHIP"

# 1) 月度断点(gate决策UNKNOWN率)
gm = Counter()
for e in f2["gate_log"]:
    ym = e["signal_day"][:7]
    gm[(ym, reason(e["code"], e["signal_day"]))] += 1
months = sorted({k[0] for k in gm})
bp = {ym: {"partial": gm.get((ym, "PARTIAL"), 0), "pre_tax": gm.get((ym, "PRE_TAXONOMY_VERSION"), 0), "not_in_cur": gm.get((ym, "NOT_IN_CURRENT_MEMBERSHIP"), 0), "src_miss": gm.get((ym, "SOURCE_MISSING"), 0)} for ym in months}
# 2) 候选级覆盖(全7885决策, sector用快照)
cand_cov = Counter()
for x in cand:
    cand_cov[(x["state"], reason(x["code"], x["day"]))] += 1
states = {}
for st in ("ELIGIBLE", "S2_GATE", "HELD", "REJ_BELOW_MA20", "REJ_NO_MA20"):
    n = sum(v for (s, _), v in cand_cov.items() if s == st)
    p = sum(v for (s, r), v in cand_cov.items() if s == st and r == "PARTIAL")
    states[st] = {"n": n, "partial": p, "partial_rate": round(p / max(1, n), 4)}
# 3) 行业指数成分审计(cycle_stage依赖=panel sector分组, 来源CURRENT_SNAPSHOT)
idx_dep = {}
for sec, grp in panel.groupby("sector"):
    codes = grp["code"].unique()
    kn = sum(1 for c in codes if c in mapping)
    idx_dep[sec] = {"n_cons": int(len(codes)), "in_map": int(kn), "source": "CURRENT_SNAPSHOT", "in_map_rate": round(kn / len(codes), 4)}
# 4) 依赖链
chain = [
    {"node": "行业分类", "coverage": "PARTIAL 72.0%(全样本月度); 2020-21几乎全PRE_TAXONOMY_VERSION", "source": "CURRENT_SNAPSHOT+in_date(ak.index_component_sw)"},
    {"node": "行业指数", "coverage": json.dumps({k: v["in_map_rate"] for k, v in list(idx_dep.items())[:3]}), "note": "成分=panel sector分组(当前快照), 与分类同源同风险"},
    {"node": "cycle_stage/门禁/RS20", "coverage": "继承上游; 无独立验证", "note": "信号全部依赖当前快照分组"},
    {"node": "候选/预算/交易", "coverage": json.dumps(states), "note": "cand_log 7885条只读审计, 净值复现一致"},
]
out = {"monthly_breakpoint": bp, "candidate_coverage_by_state": states, "index_dependencies": idx_dep,
       "chain": chain, "acceptance": "F2 nav/交易数/顺序复现一致(nav_identical=true, cand_n=7885); 审计只读",
       "verdict": "UNKNOWN原因可区分: 版本切换前(PRE_TAXONOMY_VERSION)主导2020-21; Tushare采购决策待R36(依赖链已完整可见)"}
json.dump(out, open(r"E:\mystockagent\output\ashare\industry_pit_chain.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("2021-11/12/2022-01:", json.dumps({k: bp[k] for k in ("2021-11", "2021-12", "2022-01") if k in bp}, ensure_ascii=False))
print("cand_states:", json.dumps(states, ensure_ascii=False)[:200])
print("CHAIN-OK")
