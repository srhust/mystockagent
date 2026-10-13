# -*- coding: utf-8 -*-
"""审查十四轮: ①复合拐点规则作V4入场门(st120 ∪ 复合后60日) ②快V短线策略: 跌速(dd252十日跌15pp)+基本面OK(SEC营收同比>-30%, as-of提交日)
→ 收盘上穿MA10入场, +15%止盈/-8%止损/20日限时, 快进快出。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features, build_industry_features, rule_state
from engine import run_specialist
import mu_af_v2 as M

c = M.c
f = build_features(c, [])
dd = f["dd252"]
ma20 = c.rolling(20).mean()
ma50 = c.rolling(50).mean()
st = rule_state(c)
st120 = st.rolling(120, min_periods=1).max().astype(bool)
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
s2 = ((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)).values
events, last = [], -99
for i in range(len(c)):
    if s2[i] and i - last >= 20:
        events.append(i)
        last = i
ev_ser = pd.Series(0.0, index=c.index)
ev_ser.iloc[events] = 1.0
ev60 = ev_ser.rolling(60, min_periods=1).max().astype(bool)
print(f"复合事件{len(events)}个")
for tag, sig in [("V4+阶段1过滤(对照)", (M.v4 == 1) & st120), ("V4+阶段1∪复合60日", (M.v4 == 1) & (st120 | ev60))]:
    sig2 = pd.Series(sig.astype(float), index=M.v4.index)
    r = run_specialist(M.c, sig2, M.ext85, M.cal, budget=100.0)
    eq = r[0]
    print(f"{tag}: 终值={float(eq.iloc[-1]):.2f} 笔数={len(r[1])} MDD={float((eq/eq.cummax()-1).min()):.1%}")

# ---------- ② 快V短线 ----------
ind = build_industry_features(os.path.join(config.DATA_DIR, "mu", "mu_fund.csv"), c.index)
fund_ok = (ind["rev_yoy"] > -0.30).values  # 基本面未崩(as-of提交日)
vel = (dd.diff(10) <= -0.15).values  # 十日内深跌15pp
ma10 = c.rolling(10).mean()
n = len(c)
cash, shares, in_pos, pend, entry_px = 100.0, 0.0, False, None, np.nan
equity = pd.Series(np.nan, index=c.index)
crash_start, trades = None, []
for i in range(1, n):
    px = c.iloc[i]
    if pend is not None:
        kind, d = pend
        pdx = c.iloc[d]
        if kind == "buy":
            shares = cash * (1 - 0.0015) / pdx
            entry_px = pdx
            trades.append((c.index[d], "BUY", pdx))
            cash = 0.0
            in_pos = True
        else:
            cash = shares * pdx * (1 - 0.0015)
            trades.append((c.index[d], "SELL", pdx, pdx / entry_px - 1 if entry_px else 0))
            shares = 0.0
            in_pos = False
            crash_start = None
        pend = None
    if vel[i] and not in_pos and pend is None and crash_start is None:
        crash_start = i  # 跌速确认
    if crash_start is not None and not in_pos and pend is None:
        if c.iloc[i] > ma10.iloc[i] and i - crash_start >= 2 and fund_ok[i]:
            pend = ("buy", i + 1)  # 恐慌后首次收复MA10
    if in_pos and pend is None:
        ret = px / entry_px - 1
        hold = i - pend[1] if False else None
        if ret >= 0.15 or ret <= -0.08 or (crash_start is not None and i - crash_start > 60):
            pend = ("sell", i + 1)
    equity.iloc[i] = cash + shares * px
eq = equity.dropna()
print(f"\n快V短线: 终值={float(eq.iloc[-1]):.2f} MDD={float((eq/eq.cummax()-1).min()):.1%}")
for t in trades:
    print("   " + " ".join(str(x) for x in t))
