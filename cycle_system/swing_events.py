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

# R26: S1->S0配对改为同持仓(同code)+60交易日窗口, 超窗未匹配(不无限向后找)
tidx = {d: i for i, d in enumerate(stages.index)}
lead, unmatched = [], 0
for e in s1_events:
    sec = e["sector"]
    i1 = tidx.get(e["day"])
    if i1 is None:
        continue
    matches = []
    for t in b1["trade_log"]:
        if t["reason"] == "FILLED" and t["action"] == "SELL" and sector_of.get(t["code"]) == sec:
            i2 = tidx.get(pd.Timestamp(t["sig_day"]))
            if i2 is not None and 0 <= i2 - i1 <= 60:  # 同持仓(该code)在60交易日窗口内的S0
                matches.append(i2 - i1)
    if matches:
        lead.append(min(matches))
        e["matched_hold_days"] = min(matches)
    else:
        unmatched += 1

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
    "lead_days_same_holding_60td": {"n": len(lead), "unmatched": unmatched, "median": int(np.median(lead)) if lead else None, "p25": int(np.percentile(lead, 25)) if lead else None, "p75": int(np.percentile(lead, 75)) if lead else None},
    "top_events_2122": sorted([{k: e[k] for k in ("sector", "day", "r5", "r20", "r60", "min_after60", "negative_case")} for e in seg_2122], key=lambda x: x["r60"])[:10],
}
# S2: 对B1每笔实际卖出, 判定S2条件(行业状态非good 或 个股r20<行业r20) vs 退出后20日方向
# R26: 泄漏修复——RS20=个股(t-20,t)收益-行业(t-20,t)收益(信号日收盘可知); S2=bad20 AND rs_weak(严格AND)
bad20 = (~stages.isin(("recover", "expand"))).rolling(20, min_periods=1).max().astype(bool)
rs20 = close.pct_change(20) - pd.DataFrame({c: idx[sector_of[c]].pct_change(20) for c in close.columns if sector_of.get(c) in idx.columns}).reindex(columns=close.columns)
rows = []
for t in b1["trade_log"]:
    if t["reason"] != "FILLED" or t["action"] != "SELL":
        continue
    c, sig = t["code"], pd.Timestamp(t["sig_day"])
    i0 = didx.get(sig)
    if i0 is None or i0 + 20 >= len(close.index):
        continue
    sec = sector_of.get(c, "?")
    if sec not in stages.columns or c not in rs20.columns:
        continue
    st_bad20 = bool(bad20.at[sig, sec])  # 近20交易日内行业bad(含转坏事件)
    rs_v = rs20.at[sig, c] if sig in rs20.index else np.nan
    rs_weak = bool(rs_v == rs_v and rs_v < 0)
    after20 = float(close.iloc[i0 + 20][c] / close.iloc[i0 - 1][c] - 1) if i0 > 0 else np.nan
    rows.append({"code": c, "sig": t["sig_day"], "bad20": st_bad20, "rs_weak": rs_weak, "s2": bool(st_bad20 and rs_weak), "after20_down": bool(after20 == after20 and after20 < 0), "after20": round(after20, 4) if after20 == after20 else None})
df2 = pd.DataFrame(rows)
# R26: 极端下跌识别——S1事件后20日行业跌幅<=-20%占比 vs 全体(所有行业-交易日)基线
s1r20 = [e["r20"] for e in s1ev]
base_r20 = []
for sec in idx.columns:
    v = idx[sec].pct_change(20).dropna()
    base_r20.extend(v.tolist())
out["tail20_id"] = {
    "s1_event_r20_le_-20pct": round(float(np.mean([1 if r <= -0.20 else 0 for r in s1r20])), 4),
    "baseline_r20_le_-20pct": round(float(np.mean([1 if r <= -0.20 else 0 for r in base_r20])), 4),
    "note": "极端下跌识别率对比(基线=全部行业x交易日), 处理重叠事件的统计依赖未做(如实标注)",
}
out["s2_on_b1_exits"] = {
    "n": len(df2),
    "s2_true": int(df2["s2"].sum()),
    "s2_true_after20_down_rate": round(float(df2[df2["s2"]]["after20_down"].mean()), 3) if len(df2) else None,
    "s2_false_after20_down_rate": round(float(df2[~df2["s2"]]["after20_down"].mean()), 3) if (len(df2) - int(df2["s2"].sum())) else None,
    "note": "R26泄漏修复版: RS20前向窗口+AND+bad20; 退出后方向仍为事后统计但判别输入无未来信息",
}
json.dump(out, open(r"E:\mystockagent\output\ashare\swing_sector_lead_events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(json.dumps({k: v for k, v in out.items() if k != "top_events_2122"}, ensure_ascii=False, indent=1))
print("top10 worst S1(2021-22):", json.dumps(out["top_events_2122"], ensure_ascii=False))
