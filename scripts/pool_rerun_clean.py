# -*- coding: utf-8 -*-
"""审查十六轮: 干净数据全池重跑 — 14标的×双引擎(MU用恢复的长序列): S2双路径持120日 + Fast-V v1.0(E2+X1,
无_fund.csv的标的跳过基本面过滤并标注)。输出统一机会评分表 data/allocator_scores_v3.csv。"""
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

D = os.path.join(config.DATA_DIR, "mu")
POOL = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]
rows = []
for sym in POOL:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma10, ma20, ma50 = c.rolling(10).mean(), c.rolling(20).mean(), c.rolling(50).mean()
    vol20 = c.pct_change().rolling(20).std()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    pathA = ((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)).values
    pathB = ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)).values
    ev = (pathA | pathB)
    pos = pd.Series(ev.astype(float), index=c.index).rolling(120, min_periods=1).max().astype(float).shift(1).fillna(0.0)
    ret = c.pct_change().fillna(0.0)
    eq2 = 100.0 * (1 + pos * ret).cumprod()
    m2 = float((eq2 / eq2.cummax() - 1).min())
    fin2 = float(eq2.iloc[-1])
    # Fast-V v1.0 (E2+X1); 无SEC基本面文件的标的 fund_ok=全True并标注
    fpath = os.path.join(D, f"{sym.lower()}_fund.csv")
    if os.path.exists(fpath):
        try:
            from bottom_nowcaster import build_industry_features
            ind = build_industry_features(fpath, c.index)
            fund_ok = (ind["rev_yoy"] > -0.30).values
            ftag = ""
        except Exception:
            fund_ok = np.ones(len(c), bool)
            ftag = "(fund失败全通)"
    else:
        fund_ok = np.ones(len(c), bool)
        ftag = "(无fund)"
    vel = (dd.diff(10) <= -0.15).values
    n = len(c)
    pxs = c.values
    cash, shares, in_pos, pend = 100.0, 0.0, False, None
    entry_px = stop_px = np.nan
    entry_idx, crash_start, last_end = -1, None, -999
    eqf = pd.Series(np.nan, index=c.index)
    for i in range(1, n):
        px = pxs[i]
        if pend is not None:
            kind, d = pend
            pdx = pxs[d]
            if kind == "buy":
                shares = cash * (1 - 0.0015) / pdx
                entry_px, entry_idx = pdx, d
                stop_px = pdx * (1 - 3 * float(vol20.iloc[d]))
                cash = 0.0
                in_pos = True
            else:
                cash = shares * pdx * (1 - 0.0015)
                shares = 0.0
                in_pos = False
                last_end = d
                crash_start = None
            pend = None
        if vel[i] and not in_pos and pend is None and crash_start is None and i - last_end > 90:
            crash_start = i
        if crash_start is not None and not in_pos and pend is None and i - crash_start <= 15:
            if px > ma10.iloc[i] and i - crash_start >= 2 and fund_ok[i]:
                pend = ("buy", i + 1)
        elif crash_start is not None and not in_pos and i - crash_start > 15:
            crash_start = None
        if in_pos and pend is None and (px / entry_px - 1 >= 0.15 or px <= stop_px or i - entry_idx >= 60):
            pend = ("sell", i + 1)
        eqf.iloc[i] = cash + shares * px
    eqf = eqf.ffill().fillna(100.0)
    mf = float((eqf / eqf.cummax() - 1).min())
    finf = float(eqf.iloc[-1])
    rows.append(dict(sym=sym, n=n, S2_final=round(fin2, 1), S2_mdd=f"{m2:.0%}", S2_score=round(fin2 / abs(m2)),
                     FV_final=round(finf, 1), FV_mdd=f"{mf:.0%}", FV_score=round(finf / abs(mf)), note=ftag))
    print(f"{sym}: S2 {fin2:.0f}/{m2:.0%}(评分{fin2/abs(m2):.0f}) | FastV {finf:.0f}/{mf:.0%}(评分{finf/abs(mf):.0f}){ftag}")
d = pd.DataFrame(rows).sort_values("S2_score", ascending=False)
d.to_csv(os.path.join(config.DATA_DIR, "allocator_scores_v3.csv"), index=False)
print("\n双引擎评分榜(已存 data/allocator_scores_v3.csv):")
print(d[["sym", "S2_score", "FV_score", "note"]].to_string(index=False))
