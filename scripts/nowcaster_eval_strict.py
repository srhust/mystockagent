# -*- coding: utf-8 -*-
"""审查十四轮: 统一事件评价器(修复四个P0的时间偏差)。
P0-1: 严格期限口径 20/60日 — 预警W后H日内出现合格谷底才算命中, 并报告W后最大继续下跌。
P0-2: 一对一匹配 — 每预警至多配一谷(取W后H内首个), 每谷至多配一警; 假警=同一口径(W后120日无谷)。
P0-3: 预警时间=第5个连续信号日(确认日), 不回填段首。
谷底仍为事后zigzag(评价基准允许事后定义; 特征侧as-of修复另列)。"""
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
HORIZONS = (20, 60)
CONF_N = 5  # 连续信号确认天数
print(f"规则: dd252<={DD_TH:.0%} & pos1250<={POS_TH:.0%}, 连续{CONF_N}日确认, 严格期限{HORIZONS}")
rows = []
for name, fn in STOCKS.items():
    df = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn), parse_dates=["date"]).sort_values("date").set_index("date")
    c = df["close"].astype(float)
    f = build_features(c, [])
    sig = ((f["dd252"] <= DD_TH) & (f["pos1250"] <= POS_TH)).values
    # P0-3: 事件=第5个连续信号日
    events = []
    run = 0
    for i in range(len(sig)):
        run = run + 1 if sig[i] else 0
        if run == CONF_N:
            events.append(i)
    # 谷底(事后基准)
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
    # P0-2: 一对一 — 每预警配W后H内首个谷(严格), H_MAX=120为假警判定窗
    H_MAX = 120
    used = set()
    strict = {h: 0 for h in HORIZONS}
    post_dd = []
    fa120 = 0
    for w in events:
        troughs_in = [t for t in qual if w <= t <= w + H_MAX and t not in used]
        if troughs_in:
            t0 = min(troughs_in)
            used.add(t0)
            for h in HORIZONS:
                if t0 <= w + h:
                    strict[h] += 1
            post_dd.append(float(c.iloc[w:min(len(c), w + 60)].min() / c.iloc[w] - 1))
        else:
            fa120 += 1
    # P0-1: 召回 = 每谷之前H日内存在未占用预警(一对一: 谷取最近预警)
    recall = {h: 0 for h in HORIZONS}
    wused = set()
    for t in qual:
        cands = [w for w in events if t - 60 <= w <= t and w not in wused]  # 60日窗内预警可覆盖20/60两口径
        if cands:
            w0 = max(cands)
            for h in HORIZONS:
                if w0 >= t - h:
                    recall[h] += 1
            wused.add(w0)
    r = dict(stock=name, n_ev=len(events), n_tr=len(qual),
             prec20=strict[20] / len(events) if events else np.nan, prec60=strict[60] / len(events) if events else np.nan,
             rec20=recall[20] / len(qual) if qual else np.nan, rec60=recall[60] / len(qual) if qual else np.nan,
             fa_per_yr=fa120 / ((c.index[-1] - c.index[0]).days / 365.25),
             med_dd60=np.median(post_dd) if post_dd else np.nan)
    rows.append(r)
    print(f"{name}: 事件{len(events)} 谷{len(qual)} | P@20={r['prec20']:.2f} P@60={r['prec60']:.2f} | "
          f"R@20={r['rec20']:.2f} R@60={r['rec60']:.2f} | 假警{r['fa_per_yr']:.1f}/年 | 预警后60日续跌中位{r['med_dd60']:.1%}")
ev = pd.DataFrame(rows)
ev.to_csv(os.path.join(config.DATA_DIR, "nowcaster_eval_strict.csv"), index=False)
print("\nMU重点: " + " ".join(f"{k}={v}" for k, v in rows[0].items() if k != "stock"))
