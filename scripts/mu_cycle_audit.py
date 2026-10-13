# -*- coding: utf-8 -*-
"""Phase1/MU: 历史完整上涨周期标签A(zigzag 30%交替+过滤) + V4规则覆盖审计。
周期=谷底→峰, 要求涨幅≥50%且持续≥60交易日; 类型按谷底前回撤深度分: 深跌V(>40%)/浅回撤(25-40%)/平台启动(<25%)。
事后标注仅用于评估。输出: 逐轮周期表 + V4信号覆盖(±60日) + 漏检清单。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

ZZ = 0.30   # zigzag 反转阈值
MIN_RISE, MIN_DAYS = 0.50, 60


def zigzag_turns(close, thr=ZZ):
    """标准zigzag: 跟踪当前方向极值, 反转≥thr确认转折。返回[(idx, 'T'|'P')]。"""
    c = close.values
    n = len(c)
    pivots = []
    hi_i, hi_p, lo_i, lo_p = 0, c[0], 0, c[0]
    trend = 0
    for i in range(1, n):
        x = c[i]
        if trend >= 0 and x > hi_p:
            hi_i, hi_p = i, x
        if trend <= 0 and x < lo_p:
            lo_i, lo_p = i, x
        if trend >= 0 and x <= hi_p * (1 - thr):
            pivots.append((hi_i, "P"))
            trend, lo_i, lo_p = -1, i, x
        elif trend <= 0 and x >= lo_p * (1 + thr):
            pivots.append((lo_i, "T"))
            trend, hi_i, hi_p = 1, i, x
        if trend == 0:
            # 起点方向裁决: 相对首点
            if c[0] > 0 and x <= c[0] * (1 - thr) and not pivots:
                pivots.append((0, "P"))
                trend, lo_i, lo_p = -1, i, x
            elif x >= c[0] * (1 + thr) and not pivots:
                pivots.append((0, "T"))
                trend, hi_i, hi_p = 1, i, x
    if trend == 1:
        pivots.append((hi_i, "P"))
    elif trend == -1:
        pivots.append((lo_i, "T"))
    return pivots


def main():
    mu = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "MU.csv"), parse_dates=["date"])
    mu = mu.sort_values("date").set_index("date")
    c = mu["close"].astype(float)
    print(f"MU: {c.index[0].date()} ~ {c.index[-1].date()} 共{len(c)}日 (qfq)")

    turns = zigzag_turns(c)
    rows = []
    for k in range(len(turns) - 1):
        (i0, t0), (i1, t1) = turns[k], turns[k + 1]
        if t0 != "T" or t1 != "P":
            continue
        rise = c.iloc[i1] / c.iloc[i0] - 1
        days = i1 - i0
        if rise < MIN_RISE or days < MIN_DAYS:
            continue
        # 谷底前回撤: 前一个P到本T
        prev_peak = None
        for j in range(k - 1, -1, -1):
            if turns[j][1] == "P":
                prev_peak = turns[j][0]
                break
        dd = (c.iloc[i0] / c.iloc[prev_peak] - 1) if prev_peak is not None else 0
        typ = "深跌V" if dd <= -0.40 else ("浅回撤" if dd <= -0.25 else "平台/途中启动")
        rows.append(dict(cycle_id=f"MU-{len(rows)+1:02d}", start=str(c.index[i0].date()),
                         end=str(c.index[i1].date()), rise=round(float(rise), 2), days=int(days),
                         pre_dd=round(float(dd), 2), type=typ))
    R = pd.DataFrame(rows)
    print(f"\n历史完整上涨周期(涨幅≥{MIN_RISE:.0%}, ≥{MIN_DAYS}日): {len(R)}轮")
    print(R.to_string(index=False))

    # V4规则覆盖审计
    t_lo = c.rolling(1250, min_periods=500).min()
    t_hi = c.rolling(1250, min_periods=500).max()
    pp = (c - t_lo) / (t_hi - t_lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    temp = 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)
    dd = c / c.cummax() - 1.0
    dep = (-dd).rolling(1250, min_periods=250).max()
    thr = np.maximum(0.30, 0.70 * dep)
    sig_days = np.where(((temp < 12) | (dd <= -thr)) & (temp > 0))[0]
    cov, miss = [], []
    for _, r in R.iterrows():
        i0 = c.index.get_loc(pd.Timestamp(r.start))
        hit = [s for s in sig_days if abs(s - i0) <= 60]
        if hit:
            lag = int(hit[0] - i0)
            cov.append((r.cycle_id, lag))
        else:
            miss.append((r.cycle_id, r.type, r.rise, r.pre_dd))
    print(f"\nV4覆盖(信号±60日内): {len(cov)}/{len(R)}")
    print("滞后(交易日):", {cid: lag for cid, lag in cov})
    print("漏检:", miss)
    R.to_csv(os.path.join(config.DATA_DIR, "mu", "cycles.csv"), index=False, encoding="utf-8-sig")
    print("\n已存 data/mu/cycles.csv")


if __name__ == "__main__":
    main()
