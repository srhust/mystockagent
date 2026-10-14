# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"E:\mystockagent\cycle_system")
src = open(r"E:\mystockagent\scripts\cv48_pnl.py", encoding="utf-8").read()
head = src.split("CACHE = {}")[0]
ns = {"__file__": r"E:\mystockagent\scripts\cv48_pnl.py", "__name__": "cv48dbg"}
exec(compile(head, "cv48head", "exec"), ns)
import pandas as pd
K = 600
c_full = {}
eqF, _, _ = ns["run_contract"](ns["TICKERS"], ns["SNAPS"]["12m/edge"], "V0", ns["PXd"], ns["MA100d"], ns["ev_all"], ns["LISTd"], ns["days_all"], ns["OWNPOS"], collect=c_full, reserve_mode=False, hold_days=120)
c1 = {}
eq1, _, _ = ns["run_contract"](ns["TICKERS"], ns["SNAPS"]["12m/edge"], "V0", ns["PXd"], ns["MA100d"], ns["ev_all"], ns["LISTd"], ns["days_all"][:K], ns["OWNPOS"], collect=c1, reserve_mode=False, hold_days=120)
row_prev = c_full["sh"].loc[ns["days_all"][K-1]]
print("seg1 end_state shares:", {k: round(v, 4) for k, v in c1["end_state"]["shares"].items() if v > 0})
print("full K-1 sh row:", {k: round(v, 4) for k, v in row_prev.items() if v > 0})
print("seg1 sh last row:", {k: round(v, 4) for k, v in c1["sh"].iloc[-1].items() if v > 0})
print("seg1 last idx:", c1["sh"].index[-1].date(), " K-1:", ns["days_all"][K-1].date())
