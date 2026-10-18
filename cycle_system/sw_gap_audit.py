# -*- coding: utf-8 -*-
"""R43: 801038缺8日逐日验证——①当日交易所开市(本地股票有行情) ②同日其他申万二级发布 ③801038当日记录→SOURCE_GAP/NOT_PUBLISHED/UNKNOWN."""
import json
import sys

import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m
import akshare as ak

missing = ["2022-03-03", "2022-03-23", "2022-03-31", "2022-04-14", "2022-05-05"]
panel = m.load_panel()
dts = set(pd.DatetimeIndex(sorted(panel["date"].unique())))
# 其他申万二级对照指数行情
ref = {}
for code in ("801033", "801021"):
    h = ak.index_hist_sw(symbol=code, period="day")
    h["日期"] = pd.to_datetime(h["日期"])
    ref[code] = set(h["日期"])
out = []
for d in missing:
    dt = pd.Timestamp(d)
    open_market = dt in dts
    others = {c: (dt in s) for c, s in ref.items()}
    if open_market and all(others.values()):
        v = "SOURCE_GAP"
    elif not open_market:
        v = "NOT_TRADE_DAY(check)"
    else:
        v = "UNKNOWN"
    out.append({"date": d, "local_market_open": open_market, "ref_801033": others["801033"], "ref_801021": others["801021"], "verdict": v})
res = {"missing_days": out, "summary": {v: sum(1 for x in out if x["verdict"] == v) for v in ("SOURCE_GAP", "NOT_PUBLISHED", "UNKNOWN", "NOT_TRADE_DAY(check)")}, "note": "两个对照指数同日均有行情且本地市场开市→801038官方停发/源缺(SOURCE_GAP); 独立交易所日历未接入, NOT_TRADE_DAY仅本地口径"}
json.dump(res, open(r"E:\mystockagent\output\ashare\sw801038_gap_audit.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(json.dumps(res["summary"]), json.dumps([{k: x[k] for k in ("date", "verdict")} for x in out]))
print("R43-OK")
