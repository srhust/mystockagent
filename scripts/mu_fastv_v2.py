# -*- coding: utf-8 -*-
"""审查十四轮: 快V短线执行修复 — ①单次危机单一episode(90日内不重复) ②止损=入场价×(1-3×σ20)波动自适应(治隔夜跳空穿仓) ③TP+15%/60日限时。"""
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
ma10 = c.rolling(10).mean()
ind = build_industry_features(os.path.join(config.DATA_DIR, "mu", "mu_fund.csv"), c.index)
fund_ok = (ind["rev_yoy"] > -0.30).values
vel = (dd.diff(10) <= -0.15).values
vol20 = c.pct_change().rolling(20).std()


def exits(ret, px, stop, holding_days, tp=0.15, max_hold=60):
    """审查十五轮P0: 可测的出场判定(止盈/止损/限时)。"""
    return ret >= tp or px <= stop or holding_days >= max_hold
n = len(c)
cash, shares, in_pos, pend = 100.0, 0.0, False, None
entry_px, stop_px, crash_start, last_trade_end = np.nan, np.nan, None, -999
entry_idx = -1  # 审查十五轮P0: 用索引计持仓天数(原实现日期序号相减恒False, 限时从未生效)
equity = pd.Series(np.nan, index=c.index)
trades = []
for i in range(1, n):
    px = c.iloc[i]
    if pend is not None:
        kind, d = pend
        pdx = c.iloc[d]
        if kind == "buy":
            shares = cash * (1 - 0.0015) / pdx
            entry_px = pdx
            stop_px = pdx * (1 - 3 * float(vol20.iloc[d]))
            entry_idx = d  # 审查十五轮P0: 索引持仓天数
            trades.append((c.index[d], "BUY", pdx))
            cash = 0.0
            in_pos = True
        else:
            cash = shares * pdx * (1 - 0.0015)
            trades.append((c.index[d], "SELL", pdx, pdx / entry_px - 1))
            shares = 0.0
            in_pos = False
            last_trade_end = d
            crash_start = None
        pend = None
    if vel[i] and not in_pos and pend is None and crash_start is None and i - last_trade_end > 90:
        crash_start = i
    if crash_start is not None and not in_pos and pend is None and i - crash_start <= 15:  # 审查十五轮: 急跌有效期15日(10日实测0交易, 5/10/15为待检参数)
        if c.iloc[i] > ma10.iloc[i] and i - crash_start >= 2 and fund_ok[i]:
            pend = ("buy", i + 1)
    if in_pos and pend is None:
        ret = px / entry_px - 1
        holding_days = i - entry_idx  # 审查十五轮P0修复
        if exits(ret, px, stop_px, holding_days):
            pend = ("sell", i + 1)
    equity.iloc[i] = cash + shares * px
eq = equity.dropna()
print(f"快V修复版: 终值={float(eq.iloc[-1]):.2f} MDD={float((eq/eq.cummax()-1).min()):.1%}")
for t in trades:
    print("   " + " ".join(str(x) for x in t))

