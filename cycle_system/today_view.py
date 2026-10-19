# -*- coding: utf-8 -*-
"""今日视图(只读): 用冻结代码路径原样计算最新交易日的行业四态/S2-Gate/MA20/候选与买卖建议. 不改策略, 不做新实验."""
import sys
import json

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import pandas as pd
import swing_m1 as m

panel = m.load_panel()
last_day = panel["date"].max()
p = panel[panel["date"] == last_day].copy()
sec = p.drop_duplicates("code").set_index("code")["sector"]
close = panel.pivot_table(index="date", columns="code", values="close_adj")
# 行业等权指数(与回测同路径: swing_m1内idx构建)
idx_rows = {}
for s, grp in panel.drop_duplicates(["date", "code"]).groupby("date"):
    g = grp.groupby("sector")["close_adj"].mean()
    idx_rows[s] = g
idx_all = pd.DataFrame(idx_rows).T.sort_index()
idx_all = idx_all.pct_change().fillna(0).add(1).cumprod()  # 等权收益指数(近似回测口径)
stages = m.cycle_stage(idx_all)
today_stage = stages.iloc[-1]
# S2-Gate 冻结定义
bad20 = (~stages.isin(("recover", "expand"))).rolling(20, min_periods=1).max().astype(bool)
gate_active_sectors = set()
for s in idx_all.columns:
    rs20 = idx_all[s].pct_change(20).iloc[-1] - idx_all[""].pct_change(20).iloc[-1] if "" in idx_all.columns else None
# 无全市场基准列——按回测同口径: rs20=个股20日收益-所属行业指数20日收益, gate按行业作用; 此处行业层面给bad20
gate_sectors = {s for s in idx_all.columns if bad20[s].iloc[-1]}
# 个股评分与排名(冻结3f)
rank = m.stock_rank(panel, last_day, "3f")
ma20 = close.rolling(20).mean()
c_last = close.iloc[-1]
m20_last = ma20.iloc[-1]
rows = []
for code in rank[:40]:
    s = sec.get(code)
    if s is None:
        continue
    c, m20 = c_last.get(code), m20_last.get(code)
    rows.append({"code": code, "sector": s, "score": round(float(m.stock_scores(panel, last_day, "3f").get(code, 0)), 4),
                 "gate_block": s in gate_sectors, "above_ma20": bool(c and m20 and c > m20)})
cand = [r for r in rows if not r["gate_block"] and r["above_ma20"]][:5]
blocked = [r for r in rows if r["gate_block"]][:5]
noma = [r for r in rows if (not r["gate_block"]) and (not r["above_ma20"])][:5]
stage_cn = {"expand": "扩张", "recover": "恢复", "bottom": "筑底", "weak": "走弱"}
out_lines = []
for s in idx_all.columns:
    st = today_stage[s]
    out_lines.append(f"{s}\t{stage_cn[st]}\t{'GATE停开' if s in gate_sectors else '-'}")
out = {"last_day": str(last_day.date()), "stage_lines": out_lines, "cand": cand, "blocked": blocked, "noma": noma}
json.dump(out, open(r"E:\mystockagent\output\ashare\today_view.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("LAST_DAY:", str(last_day.date()))
print("\n".join(out_lines))
print("CAND:", json.dumps(cand, ensure_ascii=False))
print("BLOCKED:", json.dumps([(b['code'], b['sector']) for b in blocked], ensure_ascii=False))
print("NOMA:", json.dumps([(b['code'], b['sector']) for b in noma], ensure_ascii=False))
