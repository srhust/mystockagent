# -*- coding: utf-8 -*-
"""V4 v2: 振幅自适应阈值 + 周期捕获率审计。
周期定义: 回撤谷底≤-30%且间隔≥250交易日; 捕获=信号日距谷底≤60交易日。
v1: 固定-50%; v2: 阈值=0.70×过去1250日最深回撤(下限30%)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

LIST = [("美光", "us_MU.csv", 2010), ("南方铜业", "us_SCCO.csv", 2015),
        ("三一重工", "stock_600031.csv", 2015), ("中国神华", "stock_601088.csv", 2015),
        ("陕西煤业", "stock_601225.csv", 2015), ("徐工机械", "stock_000425.csv", 2015),
        ("紫金矿业", "stock_601899.csv", 2015), ("温氏股份", "stock_300498.csv", 2015),
        ("江西铜业", "stock_600362.csv", 2015), ("中远海能", "stock_600026.csv", 2015),
        ("招商轮船", "stock_601872.csv", 2015)]


def load(fn):
    df = pd.read_csv(os.path.join(config.DATA_DIR, fn), parse_dates=["date"]).sort_values("date")
    return df.set_index("date")["close"].astype(float).dropna()


def thr(dd, i, mode):
    if mode == "v1":
        return 0.50
    w = dd[max(0, i - 1250):i + 1]
    dep = -np.nanmin(w) if np.isfinite(w).any() else 0.5
    return max(0.30, 0.70 * dep) if dep > 0.1 else 0.50


def run(c, mode):
    idx = c.index
    dd = (c / c.cummax() - 1.0).values
    cv, n = c.values, len(c)
    sigs, inp, ei, ep = [], False, 0, 0.0
    trades = []
    for i in range(n):
        if not np.isfinite(dd[i]):
            continue
        if not inp:
            T = thr(dd, i, mode)
            if dd[i] <= -T:
                sigs.append(i)
                inp, ei, ep = True, i, cv[i]
        else:
            if dd[i] >= -0.25 or (i - ei) > 1800 or i == n - 1:
                trades.append((idx[ei].date(), idx[i].date() if i < n - 1 else "持仓中",
                               cv[i] / ep - 1, i - ei))
                inp = False
    return dd, sigs, trades


def cycle_troughs(dd, idx, floor=-0.30, gap=250):
    tr, last = [], None
    for i in range(1, len(dd) - 1):
        if not np.isfinite(dd[i]) or dd[i] > floor:
            continue
        if dd[i] <= dd[i - 1] and dd[i] <= dd[i + 1]:
            if last is None or (idx[i] - idx[last]).days >= gap:
                tr.append(i)
                last = i
    return tr


tot = {"cyc": 0, "v1": 0, "v2": 0}
print(f"{'标的':<6}{'周期':>4}{'v1捕':>6}{'v2捕':>6}   v2交易")
for name, fn, y0 in LIST:
    c = load(fn)
    c = c[c.index >= pd.Timestamp(y0, 1, 1)]
    if len(c) < 400:
        print(f"{name}: 数据不足")
        continue
    dd, s1, t1 = run(c, "v1")
    _, s2, t2 = run(c, "v2")
    cyc = cycle_troughs(dd, c.index)

    def cap(s):
        return sum(1 for t in cyc if any(abs(s - t) <= 60 for s in s))
    tot["cyc"] += len(cyc)
    tot["v1"] += cap(s1)
    tot["v2"] += cap(s2)
    rr = [x[2] for x in t2]
    print(f"{name:<6}{len(cyc):>4}{cap(s1):>4}/{len(cyc)}{cap(s2):>4}/{len(cyc)}   "
          f"{len(t2)}笔 均值{(np.mean(rr) if rr else 0) * 100:+.1f}% "
          f"合计{(np.prod(1 + np.array(rr)) - 1) * 100 if rr else 0:+.0f}%")
    for a, b, v, d in t2:
        print(f"    {a} -> {b} {v * 100:+.1f}% ({d}天)")
print(f"\n汇总: 真实周期{tot['cyc']}轮 | v1捕获{tot['v1']}({tot['v1'] / tot['cyc'] * 100:.0f}%) "
      f"| v2捕获{tot['v2']}({tot['v2'] / tot['cyc'] * 100:.0f}%) | 目标80%")
