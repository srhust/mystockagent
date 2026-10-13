# -*- coding: utf-8 -*-
"""Phase2/MU实验B/C: V4 vs V4+突破(B) vs 突破+趋势破坏出场(C)。逐轮捕获率对比。
突破信号独立于V4(不要求temp<12)。信号T日确认 -> T+1执行(engine已保证)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from engine import run_specialist
from breakout_detector import detect_breakout
from cycle_state_machine import unified

BUDGET = 100.0
mu = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "MU.csv"), parse_dates=["date"]).sort_values("date").set_index("date")
c = mu["close"].astype(float)
cal = c.index
cycles = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "cycles.csv"))

t_lo, t_hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
pp = (c - t_lo) / (t_hi - t_lo)
r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
temp = 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)
dd = c / c.cummax() - 1.0
dep = (-dd).rolling(1250, min_periods=250).max()
thr = np.maximum(0.30, 0.70 * dep)
v4 = (((temp < 12) | (dd <= -thr)) & (temp > 0)).astype(float)

det = detect_breakout(c)
brk55 = det["brk55_x"].astype(float)

# 趋势破坏出场: 收盘跌破MA50×0.97 (仅对突破仓语义, 这里统一出场对照)
ma50 = c.rolling(50).mean()
tbreak = (c < ma50 * 0.97).astype(float)
ext_tb = pd.Series(0.0, index=cal)
ext_tb[(tbreak > 0) & (ma50.shift(1) < c.shift(1))] = 1.0  # 上穿后首次跌破, 防重复
ext_tb = ((ext_tb > 0) | (temp > 85)).astype(float)



def round_trips(fills):
    trips, cur = [], None
    for f in fills:
        d, side, sh, px = pd.Timestamp(f[0]), f[1], f[2], f[3]
        if side in ("BUY", "ADD"):
            if cur is None:
                cur = dict(first=d, spend=0.0, shares=0.0)
            cur["spend"] += sh * px
            cur["shares"] += sh
        elif side == "SELL" and cur is not None:
            cur["exit"] = d
            cur["exit_px"] = px
            trips.append(cur)
            cur = None
    if cur is not None:
        trips.append(cur)
    return trips


def attr(trip, cycles, cal):
    end = trip.get("exit") or cal[-1]
    best, bov = None, -1
    for _, r in cycles.iterrows():
        a = pd.Timestamp(r.start) - pd.Timedelta(days=180)
        b = pd.Timestamp(r.end) + pd.Timedelta(days=250)
        ov = (min(pd.Timestamp(end), b) - max(trip["first"], a)).days
        if ov > bov:
            best, bov = r.cycle_id, ov
    return best if bov > 0 else None


print(f"{'配置':<38}{'捕获中位':>8}{'捕获和':>8}{'往返':>6}{'MAE最深':>8}")
results = {}
for tag, sig, ext, tr in CONFIGS:
    eq, fills, _ = run_specialist(c, sig.reindex(cal).fillna(0), ext.reindex(cal).fillna(0),
                                  cal, budget=BUDGET, cost=0.001, tranches=tr)
    trips = round_trips(fills)
    caps, maes = [], []
    detail = {}
    for _, row in cycles.iterrows():
        mine = [t for t in trips if attr(t, cycles, cal) == row.cycle_id]
        if not mine:
            continue
        i0 = cal.get_indexer([pd.Timestamp(row.start)])[0]
        i1 = cal.get_indexer([pd.Timestamp(row.end)])[0]
        for t in mine:
            i_entry = cal.get_indexer([t["first"]])[0]
            i_end = cal.get_indexer([t.get("exit") or cal[-1]])[0]
            avg = t["spend"] / t["shares"]
            pxp = c.iloc[i_entry:i_end + 1].dropna().values
            exit_px = t.get("exit_px") or float(c.iloc[-1])
            ref_up = float(c.iloc[i1] / c.iloc[i0] - 1)
            cap = (exit_px / avg - 1) / ref_up if ref_up > 0 else np.nan
            caps.append(cap)
            maes.append(float(pxp.min() / avg - 1) if len(pxp) else np.nan)
            detail[row.cycle_id] = f"{cap*100:.0f}%"
    results[tag] = (float(np.nanmedian(caps)), float(np.nansum(caps)), len(trips), float(np.nanmin(maes)), detail)
    print(f"{tag:<38}{np.nanmedian(caps)*100:>7.0f}%{np.nansum(caps)*100:>7.0f}%{len(trips):>6}{np.nanmin(maes)*100:>7.0f}%")
print("\n逐轮捕获(配置B):", results["B: V4+突破55 无加仓 t85"][4])
print("逐轮捕获(配置C):", results["C: V4+突破55 无加仓 趋势破坏"][4])
print("逐轮捕获(配置D):", results["D: V4+突破55 30/35/35 趋势破坏"][4])


