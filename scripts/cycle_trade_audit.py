# -*- coding: utf-8 -*-
"""审查二十轮: MU九轮周期逐轮交易验收 — 每轮: A∪B事件真实入场→X2(MA100)/X3(25%跟踪)退出,
输出每轮入/出日期价格、交易收益、入场后最大继续下跌(MAE)、周期涨幅、捕获率。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features

c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "MU.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
f = build_features(c, [])
dd = f["dd252"]
ma20, ma50, ma100 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(100).mean()
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
pathA = ((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)).values
pathB = ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)).values
sig = pathA | pathB
n = len(c)
pxs = c.values
# 谷底识别(30% zigzag + 120日内涨幅≥50%)
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
lows = sorted(set(i for k, i in pivots if k == "L"))
qual = []
for t in lows:
    if n - (t + 1) < 60:
        continue
    if float(pxs[t + 1:min(n, t + 121)].max() / pxs[t] - 1) >= 0.50:
        qual.append(t)
print(f"合格谷 {len(qual)} 个: {[str(c.index[t].date()) for t in qual]}")
for mode, label in [("X2", "MA100退出"), ("X3", "25%跟踪")]:
    rows = []
    for t in qual:
        ev_i = [i for i in range(t + 1, min(n, t + 121)) if sig[i]]  # 审查二十一轮: 恢复统一120日识别窗
        if not ev_i:
            rows.append(dict(trough=str(c.index[t].date()), entry="未触发", ret=np.nan))
            continue
        e = ev_i[0]
        efill = e + 1  # T+1成交
        peak, exit_i = pxs[efill], None
        for i in range(efill + 1, n):
            peak = max(peak, pxs[i])
            if mode == "X2":
                dead = pxs[i] < ma100.iloc[i]
            else:
                dead = pxs[i] < peak * 0.75
            if dead:
                exit_i = i
                break
        x_i = exit_i if exit_i is not None else n - 1
        xfill = min(x_i + 1, n - 1)  # 审查二十二轮: 退出T+1成交
        ret = (pxs[xfill] / pxs[efill]) * (1 - 0.0015) ** 2 - 1  # 审查二十三轮: 正确双边费净收益
        mae = min(0.0, float((pxs[efill + 1:x_i + 1].min() / pxs[efill]) - 1))  # MAE≤0定义
        # 周期顶 = 谷底t至下一次30%回撤前(或序列末)的最高点; 捕获率 = 净收益 / 完整谷→顶涨幅
        nxt_h = [i for k2, i in pivots if k2 == "H" and i > t]
        top_i = nxt_h[0] if nxt_h else n - 1
        cyc = float(pxs[top_i] / pxs[t] - 1)
        cap = ret / (pxs[top_i] / pxs[t] - 1) if pxs[top_i] > pxs[t] else np.nan
        rows.append(dict(trough=str(c.index[t].date()), entry=str(c.index[e].date()), T1成交=str(c.index[efill].date()), exit成交=str(c.index[xfill].date()),
                         净收益=f"{ret:.0%}", mae=f"{mae:.0%}", 谷到顶=f"{cyc:.0%}", 捕获率=f"{cap:.0%}" if np.isfinite(cap) else "-"))
    d = pd.DataFrame(rows)
    rets = pd.to_numeric(d["净收益"].astype(str).str.rstrip("%"), errors="coerce").dropna() / 100.0
    print(f"\n=== {label}: {len(rets)}/{len(qual)}轮触发, 胜{(rets>0).sum()}/{len(rets)}, 均收益{rets.mean():.0%} ===")
    print(d.to_string(index=False))
