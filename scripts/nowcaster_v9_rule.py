# -*- coding: utf-8 -*-
"""审查十二轮v9: 规则版Nowcaster(零训练零泄漏) — dd252≤-35% 且 pos1250≤35% 即预警, 对照ML版验证dd252签名。
评价口径与v7/v8一致: 质量谷底(谷后120日涨幅≥50%), P98无——规则版天然固定阈值。"""
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

STOCKS = {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv", "AMAT": "amat.csv", "LRCX": "lrcx.csv",
          "QCOM": "qcom.csv", "TXN": "txn.csv", "INTC": "intc.csv", "KLAC": "klac.csv", "ASML": "asml.csv", "TER": "ter.csv"}
DD_TH, POS_TH = -0.35, 0.35
print(f"规则版: dd252<={DD_TH:.0%} 且 pos1250<={POS_TH:.0%}")
for name, fn in STOCKS.items():
    df = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn), parse_dates=["date"]).sort_values("date").set_index("date")
    c = df["close"].astype(float)
    f = build_features(c, [])
    sig = (f["dd252"] <= DD_TH) & (f["pos1250"] <= POS_TH)
    segs = []
    for i in range(len(c)):
        if bool(sig.iloc[i]):
            if segs and i - segs[-1][-1] <= 10:
                segs[-1].append(i)
            else:
                segs.append([i])
    segs = [s for s in segs if len(s) >= 5]  # 至少5日持续, 剔除单日噪声
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
    raw = sorted(set(i for k, i in pivots if k == "L"))
    qual = [t for t in raw if len(c.iloc[t + 1:t + 121]) >= 60 and float(c.iloc[t + 1:t + 121].max() / c.iloc[t] - 1) >= 0.50]
    hits, leads, miss = 0, [], []
    for t in qual:
        cand = [s[0] for s in segs if t - 250 <= s[0] <= t + 60]
        if cand:
            hits += 1
            leads.append(cand[-1] - t)
        else:
            miss.append(str(c.index[t].date()))
    zone = set()
    for t in qual:
        zone.update(range(max(0, t - 10), min(len(c), t + 11)))
    fa = [s for s in segs if not any(k in zone for k in s)]
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    print(f"{name}: 合格谷底{len(qual)} 召回{hits} 假警{len(fa)}({len(fa)/yrs:.1f}/年) 提前中位{int(np.median(leads)) if leads else 'NA'}日 预警段{len(segs)}")
