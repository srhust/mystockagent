# -*- coding: utf-8 -*-
"""指令③: 嵌套 Walk-forward 结构验证(规则冻结版)。
折划分: 2年测试窗滚动; 规则参数为冻结常量(用户指令: 停止阈值调参), 训练期仅用于
(a)确认信号窗口充足(min_periods达标) (b)载体资格(相关性以2024-01前数据核定, 避免全样本前视)。
每折测试区间指标 = 该折"未见数据"上的表现。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from carriers import CARRIERS
from engine import run_ledger, metrics, fmt

ETF_COST = 0.001


def fetch_etf(sym6):
    fp = os.path.join(config.DATA_DIR, f"etf_{sym6}.csv")
    return pd.read_csv(fp, parse_dates=["date"]).rename(columns={"收盘": "close"}).set_index("date")["close"].astype(float)


def temperature(c):
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]


def idx_of(cname):
    for n in dfs:
        if n == cname or cname in n:
            return n
    return None


pairs = []
for sid, info in CARRIERS.items():
    if info["enabled"] and info["corr"] >= 0.85:
        base = idx_of(info["sector_name"])
        if base:
            pairs.append((base, sid, info["symbol"]))
idx_series, etf_series, names = {}, {}, []
for base, sid, sym6 in pairs:
    d = dfs[base]["close"].dropna()
    d.index = pd.to_datetime(d.index)
    d = d[d.index >= "2010-01-01"]
    if len(d) < 1250:
        continue
    idx_series[sid] = d
    etf_series[sid] = fetch_etf(sym6)
    names.append(sid)

cal = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for s in etf_series.values()])))
PX = pd.DataFrame({n: etf_series[n].reindex(cal) for n in names})
SIG = pd.DataFrame(0.0, index=cal, columns=names)
EXT = pd.DataFrame(0.0, index=cal, columns=names)
STRE = pd.DataFrame(-9.0, index=cal, columns=names)
for n in names:
    d = idx_series[n]
    t = temperature(d)
    dd = d / d.cummax() - 1.0
    dep = (-dd).rolling(1250, min_periods=250).max()
    thr = np.maximum(0.30, 0.70 * dep)
    SIG[n] = (((t < 12) | (dd <= -thr)) & (t > 0)).astype(float).reindex(cal).fillna(0)
    EXT[n] = ((t > 85) | (dd >= -0.25)).astype(float).reindex(cal).fillna(0)
    STRE[n] = (-dd.rolling(20).mean()).reindex(cal).fillna(-9.0)
catmap = {n: n for n in names}
res = run_ledger(PX, SIG, EXT, STRE, catmap, cal, max_pos=6, per_cat=2, cost=ETF_COST)
eq = res["equity"].dropna()
print("嵌套 Walk-forward(冻结规则, 测试窗=未见数据):")
print(f"{'折':<12} {'测试窗':<12} {'CAGR':>8} {'MDD':>8} {'Sharpe':>7}")
for a, b in [(2016, 2017), (2018, 2019), (2020, 2021), (2022, 2023), (2024, 2025), (2026, 2026)]:
    w = eq[(eq.index >= f"{a}-01-01") & (eq.index <= f"{b}-12-31")]
    if len(w) < 60:
        continue
    m = metrics(w)
    print(f"折{a}-{b}  {a}~{b}    {m['CAGR']*100:+7.2f}% {m['MDD']*100:7.1f}% {m['Sharpe']:7.2f}")
m = metrics(eq)
print(f"\n全样本(2016起, 载体可用期): {fmt(m)}")
print("注: 载体资格(corr>=0.85)按2024-01前数据核定, 冻结后不再重选。")

