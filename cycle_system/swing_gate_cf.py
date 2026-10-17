# -*- coding: utf-8 -*-
"""R28: Gate拦截事件反事实(v2, 用run内gate_log)——信号价值层: 后续5/20/60日, 同code信号去重."""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

panel = m.load_panel()
close = panel.pivot(index="date", columns="code", values="close_adj")
d = json.load(open(r"E:\mystockagent\output\ashare\swing_m1_S2gate_full.json", encoding="utf-8"))
gl = d.get("gate_log", [])
didx = {dd: i for i, dd in enumerate(close.index)}

# 同code信号去重(首日)
seen, evs = set(), []
for e in gl:
    k = e["code"]
    if k in seen:
        continue
    seen.add(k)
    evs.append(e)

rows = []
print("[dbg] gl=", len(gl), "evs=", len(evs))
for e in evs:
    c, d0 = e["code"], pd.Timestamp(e["signal_day"])
    i0 = didx.get(d0)
    if i0 is None or i0 + 60 >= len(close.index) or c not in close.columns:
        continue
    try:
        p0 = close.iloc[i0 - 1][c] if i0 > 0 else close.iloc[i0][c]
        rr = {"r%d" % h: round(float(close.iloc[i0 + h][c] / p0 - 1), 4) for h in (5, 20, 60)}
    except KeyError:
        continue
    rows.append({"code": c, "day": e["signal_day"], "sector": e["sector"], "theory_alloc": e["theory_alloc"], **rr})
print("[dbg] rows=", len(rows))
df = pd.DataFrame(rows)
out = {
    "gate_events_total": len(gl),
    "unique_codes": len(df),
    "r20": {"down(<0)": int((df["r20"] < 0).sum()), "up(>5pct)": int((df["r20"] > 0.05).sum()), "flat": int(len(df)) - int((df["r20"] < 0).sum()) - int((df["r20"] > 0.05).sum()), "mean": round(float(df["r20"].mean()), 4)},
    "r60": {"down(<0)": int((df["r60"] < 0).sum()), "up(>5pct)": int((df["r60"] > 0.05).sum()), "mean": round(float(df["r60"].mean()), 4)},
    "r5_mean": round(float(df["r5"].mean()), 4),
    "worst10_r20": df.nsmallest(10, "r20").to_dict("records"),
    "best5_r20": df.nlargest(5, "r20")[["code", "day", "r20"]].to_dict("records"),
    "note": "信号价值层(同code首日去重); 组合资金价值层需分支回测, 未包含替代交易效果(如实标注)",
}
json.dump({"summary": out, "rows": rows}, open(r"E:\mystockagent\output\ashare\swing_gate_cf_v2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in out.items() if not k.startswith(("worst", "best"))}, ensure_ascii=False, indent=1))
