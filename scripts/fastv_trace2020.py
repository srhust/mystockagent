# -*- coding: utf-8 -*-
"""审查三十轮P0-1: Fast-V 2020行级状态追踪 — 完整输出冻结v1.0状态机在2020-02~05的逐日轨迹:
vel/crash_start/episode_age/MA10/fund_ok/last_end/in_pos/pend/entry_decision/reject_reason。不改任何参数。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features, build_industry_features

c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "MU.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
f = build_features(c, [])
dd = f["dd252"]
m10 = c.rolling(10).mean()
vol20 = c.pct_change().rolling(20).std()
ind = build_industry_features(os.path.join(config.DATA_DIR, "mu", "mu_fund.csv"), c.index)
fund_ok = (ind["rev_yoy"] > -0.30).values
vel = (dd.diff(10) <= -0.15).values
n = len(c)
pxs = c.values
cash, shares, in_pos, pend = 100.0, 0.0, False, None
entry_px = stop_px = np.nan
entry_idx, crash_start, last_end = -1, None, -999
trace = []
for i in range(1, n):
    d = c.index[i]
    px = pxs[i]
    if pend is not None:
        kind, d2 = pend
        pdx = pxs[d2]
        if kind == "buy":
            shares = cash * (1 - 0.0015) / pdx
            entry_px, entry_idx = pdx, d2
            stop_px = pdx * (1 - 3 * float(vol20.iloc[d2]))
            cash = 0.0
            in_pos = True
        else:
            cash = shares * pdx * (1 - 0.0015)
            shares = 0.0
            in_pos = False
            last_end = d2
            crash_start = None
        pend = None
    reason = ""
    decision = ""
    if vel[i] and not in_pos and pend is None and crash_start is None and i - last_end > 90:
        crash_start = i
        reason = "ARM急跌"
    if crash_start is not None and not in_pos and pend is None and i - crash_start <= 15:
        age = i - crash_start
        conds = {"价格>MA10": bool(px > m10.iloc[i]), "距急跌≥2日": age >= 2, "fund_ok": bool(fund_ok[i])}
        if all(conds.values()):
            pend = ("buy", i + 1)
            decision = f"入场! fill={c.index[i+1].date()}"
        else:
            decision = "拒绝:" + ",".join(k for k, v in conds.items() if not v)
    elif crash_start is not None and not in_pos and i - crash_start > 15:
        crash_start = None
        decision = "15日过期复位"
    if pd.Timestamp("2020-02-01") <= d <= pd.Timestamp("2020-05-15"):
        trace.append((d.date(), f"{px:.2f}", f"{m10.iloc[i]:.2f}", f"{dd.iloc[i]:.0%}",
                      "T" if vel[i] else ".", str(c.index[crash_start].date()) if crash_start is not None else "-",
                      f"{i - crash_start}" if crash_start is not None else "-",
                      "T" if fund_ok[i] else "F", "持有" if in_pos else "-", pend or "-", decision or reason or ""))
print(f"=== Fast-V 2020行级轨迹(冻结v1.0状态机原样) ===")
print("日期 | 收盘 | MA10 | dd252 | vel | crash_start | age | fund_ok | 持仓 | pend | 决策/拒绝原因")
for row in trace:
    print(" | ".join(str(x) for x in row))
