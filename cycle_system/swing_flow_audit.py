# -*- coding: utf-8 -*-
"""R30审计(正式版, R31入库): F2重跑(四字段日志) + 资金流向(拦截行业→通过者行业) + 分年NAV + 集中度.
复现: PYTHONPATH=仓库根; python cycle_system/swing_flow_audit.py → output/ashare/swing_flow_audit.json
口径: 流向=拦截日T+1(≤7自然日)成交差近似; 独有买入盈亏=raw收盘价区间收益(未计费用/仓位)."""
import sys, json
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m
import pandas as pd

r = m.run("expt", gate=True, exit_mode="monthly", entry_mode="daily", cap=0, alloc_policy="score", risk_mode="gate", gate_realloc="realloc")
st = r["stats"]
json.dump(r, open(r"E:\mystockagent\output\ashare\swing_m1_F2_realloc_full.json", "w", encoding="utf-8"), ensure_ascii=False)
print("F2 rerun:", json.dumps({"ret": st["return"], "dd": st["max_dd"], "calmar": st["calmar"], "gate_n": len(r["gate_log"])}))

gl = r["gate_log"]
panel = m.load_panel()
close_raw = panel.pivot(index="date", columns="code", values="close")
sector_all = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()

f0 = json.load(open(r"E:\mystockagent\output\ashare\swing_m1_B1_full.json", encoding="utf-8"))
f2f = [(t["code"], t["exec_day"], t["px"] * t["shares"]) for t in r["trade_log"] if t["reason"] == "FILLED" and t["action"] == "BUY"]
f0f = {}
for t in f0["trade_log"]:
    if t["reason"] == "FILLED" and t["action"] == "BUY":
        f0f[(t["code"], t["exec_day"])] = f0f.get((t["code"], t["exec_day"]), 0.0) + t["px"] * t["shares"]
flow = {}
for e in gl:
    sig = pd.Timestamp(e["signal_day"])
    for c, exd, amt in f2f:
        if pd.Timestamp(exd) > sig and pd.Timestamp(exd) - sig <= pd.Timedelta(days=7):
            delta = amt - f0f.get((c, exd), 0.0)
            if delta:
                sec = sector_all.get(c, "?")
                flow[sec] = flow.get(sec, 0.0) + delta
            break
flow_top = dict(sorted(flow.items(), key=lambda x: x[1], reverse=True)[:8])
flow_bot = dict(sorted(flow.items(), key=lambda x: x[1])[:8])
print("flow_in(+):", json.dumps(flow_top, ensure_ascii=False))
print("flow_out(-):", json.dumps(flow_bot, ensure_ascii=False))

navs = {}
for tag, fn in [("F0_B1", "swing_m1_B1_full.json"), ("F2", "swing_m1_F2_realloc_full.json")]:
    d = json.load(open(rf"E:\mystockagent\output\ashare\{fn}", encoding="utf-8"))
    navs[tag] = pd.Series({pd.Timestamp(x["date"]): x["nav"] for x in d["nav_log"]}).sort_index()
yearly = {}
for y in sorted(set(navs["F0_B1"].index.year)):
    row = {}
    for tag, nv in navs.items():
        v = nv[nv.index.year == y]
        row[tag] = round(float(v.iloc[-1] / v.iloc[0] - 1), 4) if len(v) > 1 else None
    yearly[y] = row
print("yearly:", json.dumps(yearly, ensure_ascii=False))

nm = pd.concat([navs[t].rename(t) for t in navs], axis=1).ffill()
mr = nm.resample("ME").last().pct_change().dropna()
mdiff = (mr["F2"] - mr["F0_B1"]).sort_values()
print("worst5_months(F2-F0):", json.dumps({str(k.date()): round(v, 4) for k, v in mdiff.head(5).items()}))
print("best5_months(F2-F0):", json.dumps({str(k.date()): round(v, 4) for k, v in mdiff.tail(5).items()}))

f2codes = {(t["code"], t["exec_day"]) for t in r["trade_log"] if t["reason"] == "FILLED" and t["action"] == "BUY"}
f0codes = {(t["code"], t["exec_day"]) for t in f0["trade_log"] if t["reason"] == "FILLED" and t["action"] == "BUY"}
sells2 = [(t["code"], t["exec_day"]) for t in r["trade_log"] if t["reason"] == "FILLED" and t["action"] == "SELL"]
own = []
did = {d: i for i, d in enumerate(close_raw.index)}
for c, bd in sorted(f2codes - f0codes):
    sel = [s for s in sells2 if s[0] == c and pd.Timestamp(s[1]) > pd.Timestamp(bd)]
    if not sel:
        continue
    i0, i1 = did.get(pd.Timestamp(bd)), did.get(pd.Timestamp(sel[0][1]))
    if i0 is None or i1 is None or i1 <= i0:
        continue
    try:
        rr = float(close_raw.iloc[i1][c] / close_raw.iloc[i0][c] - 1)
    except KeyError:
        continue
    own.append((c, str(pd.Timestamp(bd).date()), round(rr, 4)))
own.sort(key=lambda x: x[2])
print("F2-unique buys worst5(raw ret):", json.dumps(own[:5], ensure_ascii=False))
print("F2-unique buys best5(raw ret):", json.dumps(own[-5:], ensure_ascii=False))
json.dump({"flow_in": flow_top, "flow_out": flow_bot, "yearly": yearly, "own_buys": own},
          open(r"E:\mystockagent\output\ashare\swing_flow_audit.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("AUDIT-OK")
