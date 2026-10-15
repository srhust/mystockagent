# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r"E:\mystockagent\scripts")
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa
import pandas as pd
import run_shadow as RS
T = pd.Timestamp("2022-01-07")
T1 = pd.Timestamp("2022-01-10")
ts = pd.Timestamp(T.date()).tz_localize("UTC") + pd.Timedelta(hours=22)
print("created_at:", ts.isoformat())
print("valid:", RS.plan_time_valid(ts.isoformat(), T, T1))
import datetime as _dt
print("close(T):", RS.session_close_et(T), "| open(T1):", _dt.datetime.combine(T1.date(), _dt.time(9, 30)))
print("ET:", pd.Timestamp(ts).tz_convert("America/New_York"))
