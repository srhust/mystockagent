# -*- coding: utf-8 -*-
"""R27: S2-Gate被拒买单反事实——被拦截的(code, day)后续20/60日收益, 区分避免亏损vs错过上涨."""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

panel = m.load_panel()
close = panel.pivot(index="date", columns="code", values="close_adj")
sector_of = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()

# 重放S2-Gate: 收集被拦截的(code, sig_day)——重跑一次并在内存记录
# 简化: 直接用S2gate_full.json的trade_log+rej不可行(rej只计数) -> 重放run逻辑拿被拒名单:
# 用S2gate_full的trade_log FILLED名单与S0(B1)的FILLED名单差集近似不够精确(路径分化).
# 正确做法: 临时hook freeze记录rej.s2gate拦截. 此处在run外重算s2_active, 与run一致:
idx = m.industry_index(panel)
stages = m.cycle_stage(idx)
close_adj = close
bad20 = (~stages.isin(("recover", "expand"))).rolling(20, min_periods=1).max().astype(bool)
_rs_ind = pd.DataFrame({c: idx[sector_of[c]].pct_change(20) for c in close_adj.columns if sector_of.get(c) in idx.columns})
rs20 = close_adj.pct_change(20) - _rs_ind.reindex(columns=close_adj.columns)
s2_active = pd.DataFrame({c: bad20[sector_of[c]] & (rs20[c] < 0) for c in close_adj.columns if sector_of.get(c) in idx.columns}, index=close_adj.index).reindex(columns=close_adj.columns).fillna(False)

# 被拦截名单 = S0基准(B1)当日eligible∩s2_active且最终在gate版未成交——取B1的全部BUY信号日×s2_active交集(近似: B1实际买入的股票中, gate版未买入且s2_active当日为真)
b0 = json.load(open(r"E:\mystockagent\output\ashare\swing_m1_B1_full.json", encoding="utf-8"))
b1codes = {t["code"] for t in b0["trade_log"] if t["reason"] == "FILLED" and t["action"] == "BUY"}
g = json.load(open(r"E:\mystockagent\output\ashare\swing_m1_S2gate_full.json", encoding="utf-8"))
gcodes = {t["code"] for t in g["trade_log"] if t["reason"] == "FILLED" and t["action"] == "BUY"}
blocked = []
for t in b0["trade_log"]:
    if t["reason"] == "FILLED" and t["action"] == "BUY" and t["code"] not in gcodes:
        d = pd.Timestamp(t["sig_day"])
        if t["code"] in s2_active.columns and d in s2_active.index and bool(s2_active.at[d, t["code"]]):
            blocked.append((t["code"], d))
blocked = sorted(set(blocked))
didx = {d: i for i, d in enumerate(close.index)}
rows = []
for c, d in blocked:
    i0 = didx.get(d)
    if i0 is None or i0 + 60 >= len(close.index):
        continue
    try:
        p0 = close.iloc[i0 - 1][c]
        r20 = float(close.iloc[i0 + 20][c] / p0 - 1)
        r60 = float(close.iloc[i0 + 60][c] / p0 - 1)
    except KeyError:
        continue
    rows.append({"code": c, "day": str(d.date()), "r20": round(r20, 4), "r60": round(r60, 4)})
df = pd.DataFrame(rows)
out = {
    "n_blocked_unique": len(df),
    "r20": {"avoided_down(<0)": int((df["r20"] < 0).sum()), "missed_up(>5pct)": int((df["r20"] > 0.05).sum()), "flat": len(df) - int((df["r20"] < 0).sum()) - int((df["r20"] > 0.05).sum()), "mean": round(float(df["r20"].mean()), 4)},
    "r60": {"avoided_down(<0)": int((df["r60"] < 0).sum()), "missed_up(>5pct)": int((df["r60"] > 0.05).sum()), "mean": round(float(df["r60"].mean()), 4)},
    "sum_r20_if_bought": round(float(df["r20"].sum()), 3),
    "note": "反事实口径: B1买入但Gate版未买入且当日S2激活; 等权单股收益, 未计费用/仓位权重; 路径分化使名单近似(如实标注)",
}
json.dump({"summary": out, "rows": rows}, open(r"E:\mystockagent\output\ashare\swing_gate_counterfactual.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
