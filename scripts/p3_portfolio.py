# -*- coding: utf-8 -*-
"""审查十五轮P3: 组合结构检验 — 核心(V4+阶段1全仓MU策略) + 卫星(Fast-V v1.0)按0.9/0.1每日再平衡近似合成,
对照纯核心。快V权益来自fastv_f1_f2的E2模拟(独立重跑保证口径一致)。"""
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
ma10 = c.rolling(10).mean()
ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
vol20 = c.pct_change().rolling(20).std()
ind = build_industry_features(os.path.join(config.DATA_DIR, "mu", "mu_fund.csv"), c.index)
fund_ok = (ind["rev_yoy"] > -0.30).values
vel = (dd.diff(10) <= -0.15).values
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
pathA = ((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)).values

# 核心: V4+阶段1
core = run_specialist(c, ((M.v4 == 1) & rule_state(c).rolling(120, min_periods=1).max().astype(bool)).astype(float), M.ext85, M.cal, budget=100.0)[0]
core = core.reindex(c.index).ffill().fillna(100.0)
# 卫星: Fast-V E2+X1(冻结规格)
n = len(c)
cash, shares, in_pos, pend = 100.0, 0.0, False, None
entry_px = stop_px = np.nan
entry_idx, crash_start, last_end = -1, None, -999
fv = pd.Series(np.nan, index=c.index)
pxs = c.values
for i in range(1, n):
    px = pxs[i]
    if pend is not None:
        kind, d = pend
        pdx = pxs[d]
        if kind == "buy":
            shares = cash * (1 - 0.0015) / pdx
            entry_px, entry_idx = pdx, d
            stop_px = pdx * (1 - 3 * float(vol20.iloc[d]))
            cash = 0.0
            in_pos = True
        else:
            cash = shares * pdx * (1 - 0.0015)
            shares = 0.0
            in_pos = False
            last_end = d
            crash_start = None
        pend = None
    if vel[i] and not in_pos and pend is None and crash_start is None and i - last_end > 90:
        crash_start = i
    if crash_start is not None and not in_pos and pend is None and i - crash_start <= 15:
        if px > ma10.iloc[i] and i - crash_start >= 2 and fund_ok[i]:
            pend = ("buy", i + 1)
    elif crash_start is not None and not in_pos and i - crash_start > 15:
        crash_start = None
    if in_pos and pend is None and (px / entry_px - 1 >= 0.15 or px <= stop_px or i - entry_idx >= 60):
        pend = ("sell", i + 1)
    fv.iloc[i] = cash + shares * px
fv = fv.ffill().fillna(100.0)
w = M.v4.index.to_series().diff().dt.days.notna()
for frac in (0.0, 0.1, 0.2):
    port = (1 - frac) * core + frac * fv
    mdd = float((port / port.cummax() - 1).min())
    print(f"核心{1-frac:.0%}+快V卫星{frac:.0%}: 终值={float(port.iloc[-1]):.1f} MDD={mdd:.1%}")
