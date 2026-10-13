# -*- coding: utf-8 -*-
"""Phase2 A-G消融 v2 (统一状态机门控): 信号->状态机unified() -> entry/add_sig -> run_specialist。
A V4+30/35/35+t85 | B V4+突破 无加仓 t85 | C 同B趋势破坏 | D 同B分批 | E V4入场+突破顺势加仓 | F V4+突破入场+突破/回踩加仓 | G参照买持。"""
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
brk = det["brk55_x"].astype(float)
ma50 = c.rolling(50).mean()
ext_tb = ((c < ma50 * 0.97) & (ma50.shift(1) < c.shift(1)) | (temp > 85)).astype(float)
exts = {"t85": (temp > 85).astype(float), "tb": ext_tb}


def trip_stats(fills, cal, cycles, c):
    trips, cur = [], None
    for f in fills:
        d, side, sh, px = pd.Timestamp(f[0]), f[1], f[2], f[3]
        if side in ("BUY", "ADD"):
            if cur is None:
                cur = dict(first=d, spend=0.0, shares=0.0)
            cur["spend"] += sh * px
            cur["shares"] += sh
        elif side == "SELL" and cur is not None:
            cur["exit"], cur["exit_px"] = d, px
            trips.append(cur)
            cur = None
    if cur is not None:
        trips.append(cur)
    caps = []
    for t in trips:
        end = t.get("exit") or cal[-1]
        best, bov = None, -1
        for _, r in cycles.iterrows():
            a = pd.Timestamp(r.start) - pd.Timedelta(days=180)
            b = pd.Timestamp(r.end) + pd.Timedelta(days=250)
            ov = (min(pd.Timestamp(end), b) - max(t["first"], a)).days
            if ov > bov:
                best, bov = r, ov
        if bov <= 0:
            continue
        i0 = cal.get_indexer([pd.Timestamp(best.start)])[0]
        i1 = cal.get_indexer([pd.Timestamp(best.end)])[0]
        i_e = cal.get_indexer([t["first"]])[0]
        i_x = cal.get_indexer([end])[0]
        avg = t["spend"] / t["shares"]
        exit_px = t.get("exit_px") or float(c.iloc[-1])
        ref = float(c.iloc[i1] / c.iloc[i0] - 1)
        if ref > 0:
            caps.append((exit_px / avg - 1) / ref)
    return caps


CONFIGS = [
    ("A V4分批 t85", v4, None, "t85", (0.30, 0.35, 0.35)),
    ("B V4+突破 无加仓 t85", ((v4 > 0) | (brk > 0)).astype(float), None, "t85", None),
    ("C V4+突破 无加仓 趋势破坏", ((v4 > 0) | (brk > 0)).astype(float), None, "tb", None),
    ("D V4+突破 分批 趋势破坏", ((v4 > 0) | (brk > 0)).astype(float), None, "tb", (0.30, 0.35, 0.35)),
    ("E V4入场+突破顺势加仓 t85", v4, brk, "t85", (0.30,)),
    ("F V4+突破入场+突破/回踩加仓 趋势破坏", ((v4 > 0) | (brk > 0)).astype(float), brk, "tb", (0.30,)),
]
print(f"{'配置':<44}{'捕获中位':>8}{'捕获和':>8}{'往返':>6}")
for tag, sig0, addsrc, ex, tr in CONFIGS:
    if addsrc is None:
        entry, add_sig, ncyc = unified(sig0, pd.Series(0.0, index=cal), exts[ex], c)
    else:
        entry, add_sig, ncyc = unified(sig0, addsrc, exts[ex], c)
    eq, fills, _ = run_specialist(c, entry, exts[ex].reindex(cal).fillna(0), cal, budget=BUDGET,
                                  cost=0.001, tranches=(0.30, 0.35, 0.35) if tr is None else tr,
                                  add_sig=add_sig if addsrc is not None else None,
                                  add_frac=0.35, max_adds=2)
    caps = trip_stats(fills, cal, cycles, c)
    m_cagr = (eq.dropna().iloc[-1] / 100.0) ** (365.25 / (cal[-1] - cal[0]).days) - 1
    med = np.nanmedian(caps) * 100 if caps else float("nan")
    tot = np.nansum(caps) * 100 if caps else 0
    print(f"{tag:<44}{med:>7.0f}%{tot:>7.0f}%{len(caps):>6}   账户CAGR {m_cagr*100:+.2f}% 状态机轮次{ncyc}")
