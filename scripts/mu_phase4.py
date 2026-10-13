# -*- coding: utf-8 -*-
"""Phase4/MU G-lite: 产业因子可得性结论 + SOX相对强度增量实验。
结论(任务书9.1要求): DRAM/NAND现货价、库存天数、HBM结构等免费历史不可得(TrendForce/DramExchange付费,
akshare无macro_taiwan_*系列)。诚实处置: 不伪造产业数据; 用SOX相对强度(价格类, available_at=收盘后)作代理。
实验: V4入场+SOX-RS门控 vs 纯V4, 测增量。"""
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
sox = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "SOX.csv"), parse_dates=["date"]).set_index("date")["close"].astype(float)

t_lo, t_hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
pp = (c - t_lo) / (t_hi - t_lo)
r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
temp = 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)
dd = c / c.cummax() - 1.0
dep = (-dd).rolling(1250, min_periods=250).max()
thr = np.maximum(0.30, 0.70 * dep)
v4 = (((temp < 12) | (dd <= -thr)) & (temp > 0)).astype(float)
rs = (c / sox.reindex(c.index)).pct_change(20)
rs_pos = (rs > 0).astype(float).reindex(cal).fillna(0)
v4_gated = (v4 * rs_pos.shift(1)).fillna(0)  # T-1的RS状态门控(可得性: 前日收盘后已知)
ext85 = (temp > 85).astype(float)

print(f"{'配置':<28}{'CAGR':>8}{'Sharpe':>8}{'MDD':>8}{'平均暴露':>8}{'单位暴露':>9}")
for tag, sig in [("A 纯V4 (对照)", v4), ("G-lite V4+SOX-RS门控", v4_gated)]:
    eq, fills, _ = run_specialist(c, sig.reindex(cal).fillna(0), ext85.reindex(cal).fillna(0), cal,
                                  budget=BUDGET, cost=0.001, tranches=(0.30, 0.35, 0.35))
    m = metrics(eq.dropna())
    exp = eq.attrs.get("avg_exposure", 0)
    per = m["CAGR"] / exp if exp > 0 else 0
    print(f"{tag:<28}{m['CAGR']*100:>7.2f}%{m['Sharpe']:>8.2f}{m['MDD']*100:>7.1f}%{exp*100:>7.1f}%{per*100:>8.1f}%")
print("\n产业数据可得性(任务书9.1字段): DRAM/NAND=缺失(无免费源, 需TrendForce授权); "
      "SOX=可得(2014起, 日频, available_at=收盘); 台股半导体月营收=akshare无接口; 库存/HBM/盈利修正=缺失。")
