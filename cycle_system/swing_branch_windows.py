# -*- coding: utf-8 -*-
"""R29b: 共同窗口对比(F0=B1/F1_hold/F2_realloc)——固定三窗口NAV变化+平均暴露."""
import json
import pandas as pd

tags = {"F0_B1": "swing_m1_B1_full.json", "F1_hold": "swing_m1_F1_hold_full.json", "F2_realloc": "swing_m1_F2_realloc_full.json"}
wins = [("2021-09-22", "2022-03-15"), ("2021-09-22", "2022-10-31"), ("2021-09-22", "2022-11-25")]
out = {}
for tag, fn in tags.items():
    d = json.load(open(rf"E:\mystockagent\output\ashare\{fn}", encoding="utf-8"))
    nav = pd.Series({pd.Timestamp(x["date"]): x["nav"] for x in d["nav_log"]}).sort_index()
    expo = pd.Series({pd.Timestamp(x["date"]): x["n_pos"] for x in d["nav_log"]}).sort_index()
    rec = {"total_return": round(float(nav.iloc[-1] / nav.iloc[0] - 1), 4), "avg_npos": round(float(expo.mean()), 2)}
    for w0, w1 in wins:
        a = nav.loc[:pd.Timestamp(w0)].index[-1]
        b = nav.loc[:pd.Timestamp(w1)].index[-1]
        rec["win_~" + w1] = round(float(nav.at[b] / nav.at[a] - 1), 4)
        rec["npos_~" + w1] = round(float(expo.loc[a:b].mean()), 2)
    out[tag] = rec
json.dump(out, open(r"E:\mystockagent\output\ashare\swing_branch_windows.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
