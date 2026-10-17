# -*- coding: utf-8 -*-
"""R25: 行业恶化信号领先性事件研究(只读, 不改交易策略)。

S0 = 个股月末MA20x0.97(B1实际SELL信号日)  S1 = 行业四态转坏(expand/recover→其他)  S2 = S1+个股r20<行业r20(预设规则)。
事件记录: 信号日/行业60日后续收益/负例(转坏后60日仍涨)/同行业个股S0对照领先天数; 分时期(2021-22回撤段 vs 其余)。
"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

panel = m.load_panel()
close = panel.pivot(index="date", columns="code", values="close_adj")
sector_of = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()
idx = m.industry_index(panel)
stages = m.cycle_stage(idx)
didx = {d: i for i, d in enumerate(close.index)}

# S1事件池: 全行业全部转坏日(从good状态变为非good, 且要求至少持有good 20日以上防抖动)
s1_events = []
for sec in stages.columns:
    st = stages[sec]
    good_days = 0
    for d in st.index:
        if st.at[d] in ("recover", "expand"):
            good_days += 1
        else:
            if good_days >= 20:  # 预设: 持续20日以上的good转坏才算事件(防单日抖动)
                s1_events.append({"sector": sec, "day": d})
            good_days = 0

# 后续收益(行业等权指数) 5/20/60日
iidx = {d: i for i, d in enumerate(idx.index)}
for e in s1_events:
    i0 = iidx.get(e["day"])
    if i0 is None:
        continue
    for h in (5, 20, 60):
        j = i0 + h
        e[f"r{h}"] = round(float(idx.iloc[j][e["sector"]] / idx.iloc[i0][e["sector"]] - 1), 4) if j < len(idx) else None
    e["negative_case"] = (e["r60"] is not None and e["r60"] > 0)
    e["min_after60"] = round(float((idx.iloc[i0:i0 + 61][e["sector"]] / idx.iloc[i0][e["sector"]] - 1).min()), 4) if i0 + 60 < len(idx) else None

# 个股S0对照: B1实际SELL信号日(月末MA20破位)按行业聚合
b1 = json.load(open(r"E:\mystockagent\output\ashare\swing_m1_B1_full.json", encoding="utf-8"))
s0 = {}
for t in b1["trade_log"]:
    if t["reason"] == "FILLED" and t["action"] == "SELL":
        sec = sector_of.get(t["code"], "?")
        s0.setdefault(sec, []).append(pd.Timestamp(t["sig_day"]))

lead = []
for e in s1_events:
    sec = e["sector"]
    nxt = [d for d in s0.get(sec, []) if d >= e["day"]]
    if nxt:
        d0 = min(nxt)
        e["days_to_first_s0"] = int((d0 - e["day"]).days)  # 自然日近似
        lead.append(e["days_to_first_s0"])

s1ev = [e for e in s1_events if e.get("r60") is not None]
neg = sum(1 for e in s1ev if e["negative_case"])
seg_2122 = [e for e in s1ev if pd.Timestamp("2021-01-01") <= e["day"] <= pd.Timestamp("2022-12-31")]
out = {
    "s1_events_total": len(s1ev),
    "negative_cases": neg,
    "neg_rate": round(neg / max(1, len(s1ev)), 3),
    "seg_2021_22": {"n": len(seg_2122), "neg": sum(1 for e in seg_2122 if e["negative_case"]),
                     "avg_r20": round(float(np.mean([e["r20"] for e in seg_2122])), 4), "avg_r60": round(float(np.mean([e["r60"] for e in seg_2122])), 4)},
    "seg_other": {"n": len(s1ev) - len(seg_2122),
                   "avg_r20": round(float(np.mean([e["r20"] for e in s1ev if e not in seg_2122])), 4),
                   "avg_r60": round(float(np.mean([e["r60"] for e in s1ev if e not in seg_2122])), 4)},
    "lead_days_to_first_s0": {"n": len(lead), "median": int(np.median(lead)) if lead else None, "p25": int(np.percentile(lead, 25)) if lead else None, "p75": int(np.percentile(lead, 75)) if lead else None},
    "top_events_2122": sorted([{k: e[k] for k in ("sector", "day", "r5", "r20", "r60", "min_after60", "negative_case")} for e in seg_2122], key=lambda x: x["r60"])[:10],
}
# S2: 对B1每笔实际卖出, 判定S2条件(行业状态非good 或 个股r20<行业r20) vs 退出后20日方向
rows = []
for t in b1["trade_log"]:
    if t["reason"] != "FILLED" or t["action"] != "SELL":
        continue
    c, sig = t["code"], pd.Timestamp(t["sig_day"])
    i0 = didx.get(sig)
    if i0 is None or i0 + 20 >= len(close.index):
        continue
    sec = sector_of.get(c, "?")
    sec_ok = sec in stages.columns
    st_bad = bool(sec_ok and stages.at[sig, sec] not in ("recover", "expand"))
    try:
        r20_s = float(close.iloc[i0 + 20][c] / close.iloc[i0 - 1][c] - 1)
        r20_i = float(idx.at[idx.index[min(i0 + 20, len(idx) - 1)], sec] / idx.at[idx.index[max(i0 - 1, 0)], sec] - 1)
    except KeyError:
        continue
    rows.append({"code": c, "sig": t["sig_day"], "st_bad": st_bad, "rs_weak": bool(r20_s < r20_i), "s2": bool(st_bad or r20_s < r20_i), "after20_down": bool(r20_s < 0)})
df2 = pd.DataFrame(rows)
out["s2_on_b1_exits"] = {
    "n": len(df2),
    "s2_true": int(df2["s2"].sum()),
    "s2_true_after20_down_rate": round(float(df2[df2["s2"]]["after20_down"].mean()), 3) if len(df2) else None,
    "s2_false_after20_down_rate": round(float(df2[~df2["s2"]]["after20_down"].mean()), 3) if (len(df2) - int(df2["s2"].sum())) else None,
    "note": "固定B1原始卖出事件的事后判别(非可执行回测); 关口二需固定建仓+统一执行器",
}
json.dump(out, open(r"E:\mystockagent\output\ashare\swing_sector_lead_events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(json.dumps({k: v for k, v in out.items() if k != "top_events_2122"}, ensure_ascii=False, indent=1))
print("top10 worst S1(2021-22):", json.dumps(out["top_events_2122"], ensure_ascii=False))
