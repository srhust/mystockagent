# -*- coding: utf-8 -*-
"""V4 dd(-50/-25) 十一股专项: A股(baostock) + 美股(sina)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

LIST = [("美光", "us", "us_MU.csv"), ("南方铜业", "us", "us_SCCO.csv"),
        ("三一重工", "a", "stock_600031.csv"), ("中国神华", "a", "stock_601088.csv"),
        ("陕西煤业", "a", "stock_601225.csv"), ("徐工机械", "a", "stock_000425.csv"),
        ("紫金矿业", "a", "stock_601899.csv"), ("温氏股份", "a", "stock_300498.csv"),
        ("江西铜业", "a", "stock_600362.csv"), ("中远海能", "a", "stock_600026.csv"),
        ("招商轮船", "a", "stock_601872.csv")]


def v4(c):
    c = c[c.index >= "2015-01-01"]
    if len(c) < 300:
        return None
    dd = (c / c.cummax() - 1.0).values
    cv, idx, n = c.values, c.index, len(c)
    i, trs, inp = 0, [], False
    while i < n:
        if not inp and np.isfinite(dd[i]) and dd[i] <= -0.50:
            inp, ep, ei = True, cv[i], i
        elif inp:
            if dd[i] >= -0.25 or (i - ei) > 1800:
                trs.append((idx[ei].date(), idx[i].date(), cv[i] / ep - 1, i - ei))
                inp = False
        i += 1
    if inp:
        trs.append((idx[ei].date(), "持仓中", cv[-1] / ep - 1, n - 1 - ei))
    return trs


for name, mkt, fn in LIST:
    fp = os.path.join(config.DATA_DIR, fn)
    if not os.path.exists(fp):
        print(f"{name}: 无缓存")
        continue
    df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date")
    c = df.set_index("date")["close"].astype(float).dropna()
    trs = v4(c)
    if not trs:
        print(f"{name}: 0笔(最大回撤未达-50%)")
        continue
    r = [t[2] for t in trs]
    tag = "含未平仓" if trs[-1][1] == "持仓中" else ""
    print(f"{name}: {len(trs)}笔 均值{np.mean(r) * 100:+.1f}% 合计{(1 + np.prod(1 + np.array(r))) * 100 - 100:+.0f}% "
          f"胜{sum(x > 0 for x in r)}/{len(r)} {tag}")
    for a, b, v, d in trs:
        print(f"   {a} -> {b}  {v * 100:+.1f}%  ({d}天)")
