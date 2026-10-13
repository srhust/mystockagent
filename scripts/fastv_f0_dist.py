# -*- coding: utf-8 -*-
"""审查十五轮F0: 急跌后反弹分布实验(纯数据, 零阈值调参) — MU/STX/WDC全部急跌episode:
①固定持有5/10/20日的收益分布(急跌后次日买入) ②MA5/MA10收复所需天数分布(定有效期) ③收复MA5/MA10再持10日。
急跌定义: dd252十日下降≥15pp(与快V v1一致), episode=连续vel段首日, 90日去重。"""
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

def fwd(c, i, h):
    return float(c.iloc[min(len(c) - 1, i + h)] / c.iloc[i] - 1)

for name, fn in {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv"}.items():
    c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma5, ma10 = c.rolling(5).mean(), c.rolling(10).mean()
    vel = (dd.diff(10) <= -0.15).values
    n = len(c)
    eps, last = [], -999
    for i in range(1, n):
        if vel[i] and i - last > 90:
            eps.append(i)
            last = i
    rows = []
    for e in eps:
        lag5 = lag10 = None
        for j in range(e, min(n, e + 30)):
            if lag5 is None and c.iloc[j] > ma5.iloc[j]:
                lag5 = j - e
            if lag10 is None and c.iloc[j] > ma10.iloc[j]:
                lag10 = j - e
            if lag5 and lag10:
                break
        rows.append(dict(ep=c.index[e].date(), d10=float(dd.diff(10).iloc[e]),
                         h5=fwd(c, e, 5), h10=fwd(c, e, 10), h20=fwd(c, e, 20),
                         lag5=lag5 if lag5 is not None else 30, lag10=lag10 if lag10 is not None else 30))
    d = pd.DataFrame(rows)
    print(f"\n===== {name}: 急跌episode {len(d)}个 =====")
    print(f"固定持有(急跌后次日买): h5中位{d.h5.median():+.1%}/胜率{(d.h5>0).mean():.0%} | h10中位{d.h10.median():+.1%}/胜率{(d.h10>0).mean():.0%} | h20中位{d.h20.median():+.1%}/胜率{(d.h20>0).mean():.0%}")
    print(f"MA5收复天数: 中位{d.lag5.median():.0f} ≤10日占比{(d.lag5<=10).mean():.0%} ≤15日占比{(d.lag5<=15).mean():.0%} | MA10收复: 中位{d.lag10.median():.0f} ≤15日占比{(d.lag10<=15).mean():.0%}")
    if name == "MU":
        print(d.to_string(index=False))
