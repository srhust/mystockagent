# -*- coding: utf-8 -*-
"""审查十五轮: STX自救 — akshare原始数据干净(毒在qfq因子层), 自行检测拆股缺口并前向复权, 然后重跑Fast-V与双路径STX。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import akshare as ak
import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features, build_industry_features

df = ak.stock_us_daily(symbol="STX", adjust="")
df = df.reset_index().rename(columns={"date": "date", "close": "close"})[["date", "close"]]
df["date"] = pd.to_datetime(df["date"])
df = df[df["date"] >= "2009-01-01"].sort_values("date").reset_index(drop=True)
c = df["close"].astype(float).reset_index(drop=True)
# 拆股缺口检测: 隔夜 close[t-1]/close[t] 接近整数倍(2或10)
ratio = c.shift(1) / c
splits = df.loc[(ratio > 1.8) & (ratio < 12), "date"].tolist()
print("疑似拆股日:", [(d.date(), round(float(ratio.iloc[df.index[df['date'] == d][0]]), 2)) for d in splits])
adj = c.copy()
cum = 1.0
factors = np.ones(len(c))
for i in range(1, len(c)):
    r = ratio.iloc[i]
    if 1.8 < r < 12:
        cum *= r
    factors[i] = cum
c_adj = c * factors
ret = c_adj.pct_change()
print(f"复权后: 最差日{ret.min():.1%} | ≤0: {int((c_adj <= 0).sum())} | 首末: {df['date'].iloc[0].date()} {df['date'].iloc[-1].date()}")
out = pd.DataFrame({"date": df["date"], "close": c_adj})
out.to_csv(os.path.join(config.DATA_DIR, "mu", "stx.csv"), index=False)
print("已覆盖stx.csv")

# 重跑: Fast-V v1.0(STX) + 双路径(STX)
f = build_features(c_adj.set_axis(df["date"]), [])
dd = f["dd252"]
ma10, ma20, ma50 = c_adj.rolling(10).mean(), c_adj.rolling(20).mean(), c_adj.rolling(50).mean()
ind = build_industry_features(os.path.join(config.DATA_DIR, "mu", "stx_fund.csv"), f.index)
fund_ok = (ind["rev_yoy"] > -0.30).values
vel = (dd.diff(10) <= -0.15).values
n = len(c_adj)
pxs = c_adj.values
cash, shares, in_pos, pend = 100.0, 0.0, False, None
entry_px = stop_px = np.nan
entry_idx, crash_start, last_end = -1, None, -999
eq = pd.Series(np.nan, index=f.index)
led = []
for i in range(1, n):
    px = pxs[i]
    if pend is not None:
        kind, d = pend
        pdx = pxs[d]
        if kind == "buy":
            shares = cash * (1 - 0.0015) / pdx
            entry_px, entry_idx = pdx, d
            stop_px = pdx * (1 - 3 * float(c_adj.pct_change().rolling(20).std().iloc[d]))
            cash = 0.0
            in_pos = True
        else:
            cash = shares * pdx * (1 - 0.0015)
            led.append(pdx / entry_px - 1)
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
    eq.iloc[i] = cash + shares * px
eq = eq.ffill().fillna(100.0)
L = pd.Series(led)
print(f"Fast-V v1.0 STX(重验): 终值={float(eq.iloc[-1]):.1f} MDD={float((eq/eq.cummax()-1).min()):.0%} 笔{len(led)} 胜率{(L>0).mean():.0%}" if len(led) else "Fast-V STX: 0笔")
ev, lb = [], -999
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
pathA = ((c_adj > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c_adj <= ma50 * 1.05)).values
pathB = ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)).values
sig = pathA | pathB
for i in range(n):
    if sig[i] and i - lb >= 120:
        ev.append(i)
        lb = i
hi_i = lo_i = 0
hi = lo = pxs[0]
trend, pivots = 0, []
for i in range(1, n):
    x = pxs[i]
    if trend >= 0:
        if x > hi:
            hi, hi_i = x, i
        elif x <= hi * 0.70:
            pivots.append(("H", hi_i))
            trend, lo, lo_i = -1, x, i
    else:
        if x < lo:
            lo, lo_i = x, i
        elif x >= lo * 1.30:
            pivots.append(("L", lo_i))
            trend, hi, hi_i = 1, x, i
if trend < 0:
    pivots.append(("L", lo_i))
qual = [t for t in sorted(set(i for k, i in pivots if k == "L"))
        if n - (t + 1) >= 60 and float(pxs[t + 1:min(n, t + 121)].max() / pxs[t] - 1) >= 0.50]
cov = sum(1 for t in qual if any(t <= w <= t + 60 for w in ev))
print(f"双路径STX(重验): 事件{len(ev)} 合格谷{len(qual)} 精度={cov}/{len(ev)} 覆盖={cov}/{len(qual)}")
