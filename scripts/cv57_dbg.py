# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r"E:\mystockagent\scripts")
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa
import pandas as pd
import run_shadow as RS
mkt = RS.load_market()
days = mkt["days"]
KB = len(days) - 3
T = days[KB - 1]
cal = RS.us_calendar(days[0] - pd.Timedelta(days=400), pd.Timestamp.now() + pd.Timedelta(days=400))
print("T:", T.date(), "in cal:", T in cal)
if T in cal:
    i = cal.get_loc(T)
    print("cal next:", cal[i + 1].date(), "mkt next:", days[KB].date())
print("cal has mkt[KB]:", days[KB] in cal, "| mkt[KB] > T:", days[KB] > T)
