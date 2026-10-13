# -*- coding: utf-8 -*-
"""审查十四轮: 阶段2独立策略回测(入场=事件T+1执行, 出场=60日限时或阶段1状态消失) + 11股事件质量普检(事件后收益口径)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from engine import run_specialist
from bottom_nowcaster import build_features, rule_state
import mu_af_v2 as M


def stage2_events(c, st):
    st250 = st.rolling(250, min_periods=1).max().astype(bool)
    ma20 = c.rolling(20).mean()
    cond = (c > ma20) & (ma20 > ma20.shift(5)) & st250
    events, last = [], -99
    for i in range(len(c)):
        if bool(cond.iloc[i]) and i - last >= 20:
            events.append(i)
            last = i
    return events


STOCKS = {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv", "AMAT": "amat.csv", "LRCX": "lrcx.csv",
          "QCOM": "qcom.csv", "TXN": "txn.csv", "INTC": "intc.csv", "KLAC": "klac.csv", "ASML": "asml.csv", "TER": "ter.csv"}
print("=== 11股事件质量普检(事件后60日收益 vs 无条件) ===")
for name, fn in STOCKS.items():
    df = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn), parse_dates=["date"]).sort_values("date").set_index("date")
    c = df["close"].astype(float)
    st = rule_state(c)
    ev = stage2_events(c, st)
    fwd60 = c.shift(-60) / c - 1
    e60 = pd.Series([fwd60.iloc[w] for w in ev])
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    print(f"{name}: 事件{len(ev)}({len(ev)/yrs:.1f}/年) 60日中位{e60.median():.1%} 均{e60.mean():.1%} 胜率{(e60>0).mean():.0%} | 无条件{fwd60.median():.1%}")

# ---------- MU独立策略回测: 持仓=事件后60日或状态消失(取先到) ----------
c = M.c
st = rule_state(c)
ev = stage2_events(c, st)
n = len(c)
pos = np.zeros(n)
for w in ev:
    pos[w + 1:min(n, w + 61)] = 1.0  # 纯60日限时(状态消失离场版=276.63/5笔已证伪: 状态是区域标记非离场信号)
sig = pd.Series(pos, index=c.index)
r = run_specialist(M.c, sig, M.ext85, M.cal, budget=100.0)
eq = r[0]
mdd = float((eq / eq.cummax() - 1).min())
print(f"\nMU阶段2独立策略(60日限时/状态消失离场): 终值={float(eq.iloc[-1]):.2f} 笔数={len(r[1])} MDD={mdd:.1%} 持仓日={int((sig>0).sum())} 事件{len(ev)}个")
print(f"对照: V4基线523.89/-29.7% | V4+阶段1过滤528.18/-19.4%")
