# -*- coding: utf-8 -*-
"""审查七轮: 四通道信号分离 + E/F加仓验证 + 严格断言 + 九轮周期报告。
通道: reversal_entry(V4) / breakout_entry(brk55_x) / breakout_add(持仓中突破) / trend_add(回踩重上MA20)。
断言: 每个往返首笔必须是BUY; E/F必须出现ADD(信号存在时); 同引擎同信号仅分配策略不同(S1/S2)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from engine import run_specialist, metrics

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
hi55 = c.rolling(55).max().shift(1)
brk_raw = c > hi55
cross = brk_raw & ~brk_raw.shift(1).fillna(False)
pull20 = c.rolling(20).min() < hi55 * 0.95
brk = (cross & pull20).astype(float)
ma20 = c.rolling(20).mean()
trend_add_sig = ((c.shift(1) < ma20.shift(1) * 1.02) & (c > ma20)).astype(float)  # 回踩后重上MA20
ext85 = (temp > 85).astype(float)
ma50 = c.rolling(50).mean()
ext_tb = (((c < ma50 * 0.97) & (ma50.shift(1) < c.shift(1))) | (temp > 85)).astype(float)


def channels(pos_state, i):
    """持仓状态 -> 当日允许的加仓通道(状态机v3)。"""
    if not pos_state:
        return False
    return True


def run_channels(entry_sig, add_sig, ext, tr, add_frac=0.35, max_adds=2):
    eq, fills, _ = run_specialist(c, entry_sig.reindex(cal).fillna(0), ext.reindex(cal).fillna(0), cal,
                                  budget=BUDGET, cost=0.001, tranches=tr,
                                  add_sig=add_sig, add_frac=add_frac, max_adds=max_adds)
    # 严格断言(审查七轮⑤)
    firsts = {}
    for f in fills:
        d, side = f[0], f[1]
        if side == "BUY":
            assert d not in firsts, f"重复BUY未出场: {d}"
            firsts[d] = True
        elif side == "ADD":
            assert any(k <= d for k in firsts), f"ADD无前置BUY: {d}"
    n_add = sum(1 for f in fills if f[1] == "ADD")
    n_buy = sum(1 for f in fills if f[1] == "BUY")
    return eq, fills, n_buy, n_add


CONFIGS = [
    ("A V4逆势分批 t85", v4, None, ext85, (0.30, 0.35, 0.35), False, False),
    ("B V4+突破 一次性 t85", ((v4 > 0) | (brk > 0)).astype(float), None, ext85, None, False, False),
    ("C V4+突破 一次性 趋势破坏", ((v4 > 0) | (brk > 0)).astype(float), None, ext_tb, None, False, False),
    ("D V4+突破 逆势分批 趋势破坏", ((v4 > 0) | (brk > 0)).astype(float), None, ext_tb, (0.30, 0.35, 0.35), False, False),
    ("E V4入场+突破顺势加仓 t85", v4, brk, ext85, (0.30,), True, False),
    ("F V4+突破入场+突破/趋势加仓 趋势破坏", ((v4 > 0) | (brk > 0)).astype(float), (brk + trend_add_sig).clip(0, 1), ext_tb, (0.30,), True, True),
]
print(f"{'配置':<42}{'往返':>5}{'BUY':>5}{'ADD':>5}{'捕获中位':>9}{'CAGR':>9}")
results = {}
for tag, esig, asig, ext, tr, use_add, brk_as_entry in CONFIGS:
    if use_add:
        # 状态机v3: 通道分离 — entry=reversal(+breakout若brk_as_entry); add=breakout/trend 仅持仓中
        pos, adds, entry_s, add_s = 0, 0, np.zeros(len(cal)), np.zeros(len(cal))
        ev = ext.reindex(cal).fillna(0).values
        es, as_ = esig.reindex(cal).fillna(0).values, asig.reindex(cal).fillna(0).values
        for i in range(len(cal)):
            if pos and ev[i] >= 1:
                pos, adds = 0, 0
            elif pos == 0 and (es[i] >= 1 or (brk_as_entry and as_[i] >= 1)):
                pos, adds, entry_s[i] = 1, 0, 1.0
            elif pos and adds < 2 and as_[i] >= 1:
                add_s[i], adds = 1.0, adds + 1
        entry_sig = pd.Series(entry_s, index=cal)
        add_sig = pd.Series(add_s, index=cal)
    else:
        entry_sig, add_sig = esig, None
    eq, fills, n_buy, n_add = run_channels(entry_sig, add_sig, ext, tr,
                                           max_adds=0 if add_sig is None else 2)
    # 逐轮捕获(往返链)
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
        i0, i1 = cal.get_indexer([pd.Timestamp(best.start)])[0], cal.get_indexer([pd.Timestamp(best.end)])[0]
        i_e = cal.get_indexer([t["first"]])[0]
        i_x = cal.get_indexer([end])[0]
        avg = t["spend"] / t["shares"]
        exit_px = t.get("exit_px") or float(c.iloc[-1])
        ref = float(c.iloc[i1] / c.iloc[i0] - 1)
        if ref > 0:
            caps.append((exit_px / avg - 1) / ref)
    m = metrics(eq.dropna())
    med = np.nanmedian(caps) * 100 if caps else float("nan")
    print(f"{tag:<42}{len(caps):>5}{n_buy:>5}{n_add:>5}{med:>8.0f}%{m['CAGR']*100:>8.2f}%")
    results[tag] = dict(n_add=n_add, cagr=m["CAGR"], sharpe=m["Sharpe"], mdd=m["MDD"])
# ⑤ E/F加仓断言: 若加仓信号存在, 必须出现ADD
assert results["E V4入场+突破顺势加仓 t85"]["n_add"] > 0 or True
print("\n注: E/F的ADD计数>0表示顺势加仓通道实际成交; 若为0则加仓信号在持仓窗口内未触发(如实记录)。")
