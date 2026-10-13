# -*- coding: utf-8 -*-
"""Phase1/MU消融: V4入场 × 仓位方案(一次性/30-35-35) × 出场(t85/t85dd/t75dd) 逐轮回放。
输出每周期: 收益/年化/MFE/MAE/捕获率/回吐/资金占用/持有天数。同MU全序列专户, fills按日期归属周期。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from engine import run_specialist
from cycle_metrics import cycle_report

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
sig = (((temp < 12) | (dd <= -thr)) & (temp > 0)).astype(float)
exts = {"t85": (temp > 85).astype(float),
        "t85dd": ((temp > 85) | (dd >= -0.25)).astype(float),
        "t75dd": ((temp > 75) | (dd >= -0.25)).astype(float)}

CONFIGS = [("一次性-t85", None, "t85"), ("一次性-t85dd", None, "t85dd"), ("一次性-t75dd", None, "t75dd"),
           ("30/35/35-t85", (0.30, 0.35, 0.35), "t85"), ("30/35/35-t85dd", (0.30, 0.35, 0.35), "t85dd"),
           ("30/35/35-t75dd", (0.30, 0.35, 0.35), "t75dd")]
dates_list = list(cal)


def round_trips(fills, c):
    """专户fills -> 往返链: [(first_buy, last_buy, exit或None, spend, shares)]。"""
    trips, cur = [], None
    for f in fills:
        d, side, sh, px = pd.Timestamp(f[0]), f[1], f[2], f[3]
        if side in ("BUY", "ADD"):
            if cur is None:
                cur = dict(first=d, last=d, spend=0.0, shares=0.0)
            cur["last"] = d
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


def attr_cycle(trip, cycles, cal):
    """往返链归属: 与[start-180d, end+250d]重叠最多的周期; 未平仓归最后覆盖期。"""
    end = trip.get("exit") or cal[-1]
    best, best_ov = None, -1
    for _, r in cycles.iterrows():
        a = pd.Timestamp(r.start) - pd.Timedelta(days=180)
        b = pd.Timestamp(r.end) + pd.Timedelta(days=250)
        ov = (min(pd.Timestamp(end), b) - max(trip["first"], a)).days
        if ov > best_ov:
            best, best_ov = r.cycle_id, ov
    return best if best_ov > 0 else None


for tag, tr, ex in CONFIGS:
    eq, fills, _ = run_specialist(c, sig.reindex(cal).fillna(0), exts[ex].reindex(cal).fillna(0),
                                  cal, budget=BUDGET, cost=0.001, tranches=tr)
    trips = round_trips(fills, c)
    print(f"\n=== {tag} ===  (往返{len(trips)}次)")
    for _, row in cycles.iterrows():
        mine = [t for t in trips if attr_cycle(t, cycles, cal) == row.cycle_id]
        if not mine:
            print(f"{row.cycle_id} {row.type}: 无入场(漏检)")
            continue
        i0 = cal.get_indexer([pd.Timestamp(row.start)])[0]
        i1 = cal.get_indexer([pd.Timestamp(row.end)])[0]
        for t in mine:
            i_entry = cal.get_indexer([t["first"]])[0]
            i_end = cal.get_indexer([t.get("exit") or cal[-1]])[0]
            avg_cost = t["spend"] / t["shares"]
            px_path = c.iloc[i_entry:i_end + 1].dropna().values
            exit_px = t.get("exit_px") or float(c.iloc[-1])
            ret = exit_px / (avg_cost * 1.001) - 1
            mfe = float(px_path.max() / avg_cost - 1) if len(px_path) else np.nan
            mae = float(px_path.min() / avg_cost - 1) if len(px_path) else np.nan
            ref_up = float(c.iloc[i1] / c.iloc[i0] - 1)
            capture = (exit_px / avg_cost - 1) / ref_up if ref_up > 0 else np.nan
            peak_float = float(px_path.max()) if len(px_path) else exit_px
            giveback = (peak_float - exit_px) / peak_float if peak_float > 0 else np.nan
            days = (t.get("exit") or cal[-1]) - t["first"]
            ann = (1 + ret) ** (365.25 / max(1, days.days)) - 1 if days.days > 5 else np.nan
            print(f"{row.cycle_id} {row.type}: 收益{ret*100:+.0f}% 年化{ann*100:+.0f}% 持有{days.days}日 "
                  f"MFE{mfe*100:+.0f}% MAE{mae*100:+.0f}% 捕获{capture*100:.0f}% 回吐{giveback*100:.0f}% "
                  f"投入{t['spend']:.0f}({t['spend']/BUDGET*100:.0f}%)" + ("" if t.get("exit") else " [未平仓]"))
