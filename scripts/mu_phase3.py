# -*- coding: utf-8 -*-
"""Phase3/MU暴露匹配: 同等平均暴露下比较仓位方案, 分离分批机制的真实贡献。
A 30/35/35逆势 | A2 一次性30%(同暴露无加仓) | A3 一次性100% | A4 30%首仓+突破/回踩顺势加仓(状态机)。
单位暴露收益 = 账户CAGR / 平均暴露。"""
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
ext85 = (temp > 85).astype(float)
entry, add_sig, ncyc = unified(v4, brk, ext85, c)

CONFIGS = [
    ("A  30/35/35 逆势(基准)", v4, None, (0.30, 0.35, 0.35), None, 0),
    ("A2 一次性30% (同暴露)", v4, None, (0.30,), None, 0),
    ("A3 一次性100%", v4, None, (1.0,), None, 0),
    ("A4 30%首仓+顺势加仓(状态机)", v4, (0.30,), (0.30,), add_sig, 2),
]
print(f"{'配置':<30}{'CAGR':>8}{'Sharpe':>8}{'MDD':>8}{'平均暴露':>8}{'单位暴露收益':>10}")
from engine import metrics
for tag, sig, tr_ign, tr, addsig, mx in CONFIGS:
    eq, fills, _ = run_specialist(c, sig.reindex(cal).fillna(0), ext85.reindex(cal).fillna(0), cal,
                                  budget=BUDGET, cost=0.001, tranches=tr, add_sig=addsig, add_frac=0.35, max_adds=mx)
    m = metrics(eq.dropna())
    exp = eq.attrs.get("avg_exposure", float("nan"))
    per_exp = m["CAGR"] / exp if exp > 0 else float("nan")
    print(f"{tag:<30}{m['CAGR']*100:>7.2f}%{m['Sharpe']:>8.2f}{m['MDD']*100:>7.1f}%{exp*100:>7.1f}%{per_exp*100:>9.1f}%")
