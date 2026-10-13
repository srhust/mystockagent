# -*- coding: utf-8 -*-
"""审查十四轮: 两阶段评价 — 阶段1=深跌区规则状态(已定位为风险过滤), 阶段2=下跌趋势结束确认(MA20收复+MA20上行, 需阶段1近250日内激活过)。
严格口径: 确认事件w覆盖谷t当且仅当 t <= w <= t+60 (确认天然滞后于谷); P=覆盖谷的事件占比, R=被覆盖谷占比, 滞后中位=w-t。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features, rule_state, trough_confirm_idx

STOCKS = {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv", "AMAT": "amat.csv", "LRCX": "lrcx.csv",
          "QCOM": "qcom.csv", "TXN": "txn.csv", "INTC": "intc.csv", "KLAC": "klac.csv", "ASML": "asml.csv", "TER": "ter.csv"}
# P0-4复核: days_since_prev_trough 是否逐日递增(审查者质疑点)
_c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "MU.csv"), parse_dates=["date"]).set_index("date")["close"].astype(float)
probe_tr = [3000]
ff = build_features(_c, probe_tr)
i0 = 3000
print(f"P0-4复核: 谷底日dist={ff['days_since_prev_trough'].iloc[i0]} T+1={ff['days_since_prev_trough'].iloc[i0+1]} T+5={ff['days_since_prev_trough'].iloc[i0+5]} (逐日递增={'是' if ff['days_since_prev_trough'].iloc[i0+5] == 5 else '否'})")
print("阶段2规则: 阶段1状态近250日内出现过 且 收盘>MA20 且 MA20较5日前上行, 事件去重20日")
for name, fn in STOCKS.items():
    df = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn), parse_dates=["date"]).sort_values("date").set_index("date")
    c = df["close"].astype(float)
    st = rule_state(c)
    st250 = st.rolling(250, min_periods=1).max().astype(bool)
    ma20 = c.rolling(20).mean()
    cond = (c > ma20) & (ma20 > ma20.shift(5)) & st250
    events = []
    last = -99
    for i in range(len(c)):
        if bool(cond.iloc[i]) and i - last >= 20:
            events.append(i)
            last = i
    # 谷底+确认时点
    hi_i = lo_i = 0
    hi = lo = c.iloc[0]
    trend, pivots = 0, []
    for i in range(1, len(c)):
        x = c.iloc[i]
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
            if len(c.iloc[t + 1:t + 121]) >= 60 and float(c.iloc[t + 1:t + 121].max() / c.iloc[t] - 1) >= 0.50]
    conf = trough_confirm_idx(c, qual)
    used, cover, lags = set(), 0, []
    for w in events:
        ts = [t for t in qual if t <= w <= t + 60 and t not in used]
        if ts:
            t0 = max(ts)
            used.add(t0)
            cover += 1
            lags.append(w - t0)
    rec = len(used)
    print(f"{name}: 确认事件{len(events)} 合格谷{len(qual)} | P={cover}/{len(events)} R={rec}/{len(qual)} | 谷→确认滞后中位{int(np.median(lags)) if lags else 'NA'}日 | zigzag确认滞后中位{int(np.nanmedian(conf - np.array(qual))) if len(qual) else 'NA'}日")
