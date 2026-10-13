# -*- coding: utf-8 -*-
"""V4 v2.1: 自适应阈值 + 修复段再武装(消鞭锯) + 真实周期分母(谷底间须有修复)。
目标: 捕获率>=80% 且单笔均值不劣于v1。"""
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


def real_cycles(dd, idx, floor=-0.30):
    """真实周期: 谷底<=floor, 且与下一谷底之间存在修复段(dd>=-0.15)。"""
    mins = [i for i in range(1, len(dd) - 1)
            if np.isfinite(dd[i]) and dd[i] <= floor and dd[i] <= dd[i - 1] and dd[i] <= dd[i + 1]]
    out = []
    for i in mins:
        if out:
            seg = dd[out[-1]:i]
            if not (np.isfinite(seg) & (seg >= -0.15)).any():
                # 无修复: 取更深者
                if dd[i] < dd[out[-1]]:
                    out[-1] = i
                continue
        out.append(i)
    return out


def run_v21(c):
    idx = c.index
    dd = (c / c.cummax() - 1.0).values
    cv, n = c.values, len(c)
    trades, inp, ei, ep, armed = [], False, 0, 0.0, True
    for i in range(n):
        if not np.isfinite(dd[i]):
            continue
        if not inp:
            w = dd[max(0, i - 1250):i + 1]
            dep = -np.nanmin(w) if np.isfinite(w).any() else 0.5
            T = max(0.30, 0.70 * dep) if dep > 0.1 else 0.50
            if armed and dd[i] <= -T:
                trades.append([i, None, cv[i], T])
                inp, ei, ep, armed = True, i, cv[i], False
        else:
            if dd[i] >= -0.25 or (i - ei) > 1800 or i == n - 1:
                trades[-1][1] = i
                inp = False
        if not inp and dd[i] >= -0.15:
            armed = True
    out = []
    for s, e, ep, T in trades:
        e = e if e is not None else n - 1
        out.append((s, e, cv[e] / ep - 1, e - s, T))
    return dd, out


def cycle_hit(cyc, trades):
    k = 0
    for t in cyc:
        if any(abs(s - t) <= 60 for s, _, _, _, _ in trades):
            k += 1
    return k


tot = {"cyc": 0, "hit": 0}
print(f"{'标的':<6}{'真实周期':>6}{'v2.1捕获':>8}   交易")
for name, fn, y0 in LIST:
    c = load(fn)
    c = c[c.index >= pd.Timestamp(y0, 1, 1)]
    if len(c) < 400:
        continue
    dd, trs = run_v21(c)
    cyc = real_cycles(dd, c.index)
    hit = cycle_hit(cyc, trs)
    tot["cyc"] += len(cyc)
    tot["hit"] += hit
    rr = [x[2] for x in trs]
    closed = [x for x in trs if str(x[1]) != "持仓中" or True]
    print(f"{name:<6}{len(cyc):>6}{hit:>6}/{len(cyc)}   {len(trs)}笔 "
          f"均值{(np.mean(rr) if rr else 0) * 100:+.1f}% 合计{(np.prod(1 + np.array(rr)) - 1) * 100 if rr else 0:+.0f}%")
    for a, b, v, d, T in trs:
        print(f"    {c.index[a].date()} -> {c.index[b].date()} {v * 100:+.1f}% ({d}天 T={T * 100:.0f}%)")
print(f"\n汇总: 真实周期{tot['cyc']}轮 捕获{tot['hit']}({tot['hit'] / tot['cyc'] * 100:.0f}%) 目标80%")
