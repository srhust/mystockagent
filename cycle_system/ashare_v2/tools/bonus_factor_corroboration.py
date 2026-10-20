# -*- coding: utf-8 -*-
"""BONUS旁证v2: 复合除权公式。sina hfq因子日环比 = (1+r) * prev_close/(prev_close - cash_ps)
   (r=当日送转合计, cash=当日每股税前派息; 纯送转日 cash=0 → 1+r)。err>1%=不一致。"""
import json, glob, os, sys
import pandas as pd
sys.path.insert(0, ".")
from cycle_system.ashare_v2 import _path
from cycle_system.ashare_v2 import config as C

pool = os.path.join(C.DATA_DIR, "ashare_history_2000/corporate_actions/full_replay_pool")
rows, n_bad = [], 0
for f in glob.glob(os.path.join(pool, "*.events.json")):
    j = json.load(open(f, encoding="utf-8"))
    by_ex = {}
    for e in j["events"]:
        if e["type"] in ("BONUS", "DIVIDEND"):
            d = by_ex.setdefault(e["ex_date"], {"r": 0.0, "cash": 0.0})
            if e["type"] == "BONUS":
                d["r"] += float(e["ratio"])
            else:
                d["cash"] += float(e["cash_per_share"])
    bonus_days = {k: v for k, v in by_ex.items() if v["r"] > 0}
    if not bonus_days:
        continue
    pure = j["_meta"]["code"].split(".")[1]
    p = os.path.join(C.STOCKS_DIR, f"{pure}.csv")
    if not os.path.exists(p):
        continue
    df = pd.read_csv(p, dtype=str)
    df["date"] = df["date"].str[:10]
    df = df.sort_values("date").reset_index(drop=True)
    for ex, v in bonus_days.items():
        i = df.index[df.date == ex]
        if not len(i) or i[0] == 0:
            rows.append({"code": pure, "ex": ex, "status": "NOT_IN_PANEL"})
            continue
        i0 = i[0]
        prev_c = float(df.close.iloc[i0 - 1])
        fac_t, fac_p = float(df.adj_factor.iloc[i0]), float(df.adj_factor.iloc[i0 - 1])
        expect = (1 + v["r"]) * prev_c / (prev_c - v["cash"]) if prev_c > v["cash"] else None
        if expect is None:
            rows.append({"code": pure, "ex": ex, "status": "CASH_GE_PREV_WEIRD"})
            continue
        obs = fac_t / fac_p
        err = abs(obs - expect) / expect
        ok = err <= 0.01
        n_bad += 0 if ok else 1
        rows.append({"code": pure, "ex": ex, "r": v["r"], "cash": v["cash"],
                     "expect": round(expect, 6), "obs": round(obs, 6),
                     "err_pct": round(err * 100, 3), "status": "CONSISTENT" if ok else "INCONSISTENT"})
out = {"probe": "CAI.4 v2 复合除权旁证(送转×现金)", "n": len(rows), "inconsistent": n_bad,
       "rows": rows, "verdict": "ALL_CONSISTENT" if n_bad == 0 else "HAS_INCONSISTENT"}
D = os.path.join(C.DATA_DIR, "ashare_history_2000/QC")
old = os.path.join(D, "BONUS_factor_consistency.json")
json.dump({"_superseded_by": "v2", "_v1_flaw": "纯(1+ratio)公式误用于同日送转+派息复合事件(10例全部为送转+派息同日)",
           "probe": "CAI.4 v1", "rows": json.load(open(old, encoding="utf-8"))["rows"][:5]},
          open(os.path.join(D, "BONUS_factor_consistency_v1.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
json.dump(out, open(old, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps({"n": out["n"], "inconsistent": out["inconsistent"], "verdict": out["verdict"]}))
for r in rows:
    if r.get("status") != "CONSISTENT":
        print(r)
