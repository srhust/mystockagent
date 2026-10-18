# -*- coding: utf-8 -*-
"""R34: PIT覆盖审计v2——①映射多记录+冲突检测(不覆盖) ②信号日口径统一(gate/buy/sell均sig_day, 双口径报告) ③全样本月度截面 ④分时期+重点行业分层."""
import json
import sys

import akshare as ak
import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")

# 1) 映射v2: list保留全部记录, 同code跨行业→CONFLICT
info = ak.sw_index_second_info()
nc, cc = [c for c in info.columns if "名称" in c][0], [c for c in info.columns if "代码" in c][0]
mapping, conflicts = {}, []
for _, rr in info.iterrows():
    code = str(rr[cc]).replace(".SI", "")
    try:
        df = ak.index_component_sw(symbol=code)
        if df is None or len(df) == 0 or "计入日期" not in df.columns:
            continue
        for _, r2 in df.iterrows():
            sc = str(r2.get("证券代码", "")).zfill(6)
            d = str(r2.get("计入日期", ""))
            if not (sc and d[:4].isdigit()):
                continue
            rec = {"industry_code": code, "industry": rr[nc], "in_date": d, "source": "ak.index_component_sw"}
            if sc in mapping:
                if all(x["industry_code"] != code for x in mapping[sc]):
                    conflicts.append({"code": sc, "records": mapping[sc] + [rec]})
                mapping[sc].append(rec)
            else:
                mapping[sc] = [rec]
    except Exception:
        continue
print("map_n:", len(mapping), "conflicts:", len(conflicts))
json.dump({"mapping": mapping, "conflicts": conflicts}, open(r"E:\mystockagent\output\ashare\industry_member_map_v2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 2) 决策级(信号日口径+双口径)
f2 = json.load(open(r"E:\mystockagent\output\ashare\swing_m1_F2_realloc_full.json", encoding="utf-8"))
sect_of = {e["code"]: e["sector"] for e in f2["gate_log"]}
dec = {"gate": [(e["code"], e["signal_day"]) for e in f2["gate_log"]],
       "buy": [(t["code"], t["sig_day"]) for t in f2["trade_log"] if t.get("reason") == "FILLED" and t["action"] == "BUY"],
       "sell": [(t["code"], t["sig_day"]) for t in f2["trade_log"] if t.get("reason") == "FILLED" and t["action"] == "SELL"]}

def state(c, d):
    recs = mapping.get(c)
    if not recs:
        return "UNKNOWN"
    ok = any(pd.Timestamp(r["in_date"]) <= pd.Timestamp(d) for r in recs)
    return "PARTIAL" if ok else "UNKNOWN"  # 冲突股按PARTIAL计(保守: 有任一可知记录), 冲突单独计数

def bucket(d):
    y = pd.Timestamp(d).year
    return "2020-2021" if y <= 2021 else ("2022-2023" if y <= 2023 else "2024-2026")

PERIODS, KEYSEC = ("2020-2021", "2022-2023", "2024-2026"), ("农化制品", "证券", "房地产开发", "工业金属", "煤炭开采")
res = {}
for kind, s in dec.items():
    st = [state(c, d) for c, d in s]
    per = {p: {"n": 0, "partial": 0, "unknown": 0} for p in PERIODS}
    sec = {}
    for (c, d), x in zip(s, st):
        b = bucket(d)
        per[b]["n"] += 1
        per[b]["partial" if x == "PARTIAL" else "unknown"] += 1
        sc = sect_of.get(c, "?")
        sec.setdefault(sc, {"n": 0, "partial": 0})[ "n"] += 1
        if x == "PARTIAL":
            sec[sc]["partial"] += 1
    res[kind] = {"n": len(s), "partial": st.count("PARTIAL"), "unknown": st.count("UNKNOWN"),
                 "partial_membership_coverage": round(st.count("PARTIAL") / max(1, len(s)), 4),
                 "verified_pit_coverage": 0.0,
                 "by_period": {p: {**v, "partial_rate": round(v["partial"] / max(1, v["n"]), 3)} for p, v in per.items()},
                 "key_sectors": {k: {"n": v["n"], "partial_rate": round(v["partial"] / max(1, v["n"]), 3)} for k, v in sec.items() if k in KEYSEC}}
# 3) 双口径(执行日)对照
dec_exec = {"buy": [(t["code"], t["exec_day"]) for t in f2["trade_log"] if t.get("reason") == "FILLED" and t["action"] == "BUY"]}
res["buy_execday_ref"] = {"n": len(dec_exec["buy"]), "partial": sum(state(c, d) == "PARTIAL" for c, d in dec_exec["buy"]), "note": "执行日口径对照(审查者P1: 信号日为准, 此列仅示口径差异)"}
# 4) 全样本月度截面(249股x78月)
import swing_m1 as m
panel = m.load_panel()
codes = sorted(panel.drop_duplicates("code")["code"])
months = pd.date_range("2020-04-30", "2026-09-30", freq="ME")
allst = {"n": 0, "partial": 0, "conflict": 0, "unknown": 0}
for d in months:
    ds = str(d.date())
    for c in codes:
        allst["n"] += 1
        recs = mapping.get(c)
        if not recs:
            allst["unknown"] += 1
        elif len(recs) > 1:
            allst["conflict"] += 1 if len({r["industry_code"] for r in recs}) > 1 else 0
            allst["partial" if any(pd.Timestamp(r["in_date"]) <= d for r in recs) else "unknown"] += 1
        else:
            allst["partial" if pd.Timestamp(recs[0]["in_date"]) <= d else "unknown"] += 1
allst["partial_rate"] = round(allst["partial"] / max(1, allst["n"]), 4)
out = {"term": "partial_membership_coverage(非严格Coverage_PIT; verified恒为0——无历史可追溯来源)",
       "signal_day_decision": res, "full_sample_monthly": allst, "conflicts_n": len(conflicts), "mapping_v2": "list结构不覆盖, 冲突单列",
       "verdict": "审计范围仍=已记录执行事件+全样本月度截面近似; 行业过滤前候选/行业指数成分依赖未含(需R35完整候选回放)",
       "limitation": "B0/B1/F2冻结, 不接入策略, 不跑收益实验"}
json.dump(out, open(r"E:\mystockagent\output\ashare\industry_pit_coverage_v2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("gate:", json.dumps(res["gate"]["by_period"], ensure_ascii=False)[:200])
print("full:", json.dumps(allst), "COV2-OK")
