# -*- coding: utf-8 -*-
"""调查跟踪异常: 煤炭(0.744/-19.2pp) 与 房地产(0.291) 指数 vs ETF。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import data  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]

for sector, sym in [("煤炭", "sh515220"), ("房地产", "sh512200"), ("钢铁", "sh515210")]:
    idx = dfs[sector]["close"]
    etf = data.get_etf_hist(sym).set_index("date")["close"]
    df = pd.DataFrame({"idx": idx, "etf": etf}).dropna()
    df = df[df.index >= "2021-07-01"]
    r = df.pct_change().dropna()
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    ann_i = (df["idx"].iloc[-1] / df["idx"].iloc[0]) ** (1 / yrs) - 1
    ann_e = (df["etf"].iloc[-1] / df["etf"].iloc[0]) ** (1 / yrs) - 1
    print(f"\n=== {sector} {sym} ({df.index[0].date()}~{df.index[-1].date()}, {yrs:.2f}年) ===")
    print(f"  指数年化 {ann_i * 100:+.1f}%  ETF年化 {ann_e * 100:+.1f}%  相关 {r['idx'].corr(r['etf']):.3f}")
    print(f"  指数首末: {df['idx'].iloc[0]:.1f} -> {df['idx'].iloc[-1]:.1f}")
    print(f"  ETF  首末: {df['etf'].iloc[0]:.3f} -> {df['etf'].iloc[-1]:.3f}")
    # 分年度收益对比
    for y in range(2021, 2027):
        sub = df[df.index.year == y]
        if len(sub) > 20:
            ri = sub["idx"].iloc[-1] / sub["idx"].iloc[0] - 1
            re = sub["etf"].iloc[-1] / sub["etf"].iloc[0] - 1
            print(f"  {y}: 指数 {ri * 100:+6.1f}%  ETF {re * 100:+6.1f}%  差 {(ri - re) * 100:+6.1f}pp")
    # 单日最大分歧
    r["diff"] = (r["idx"] - r["etf"]).abs()
    worst = r.nlargest(3, "diff")
    for d, row in worst.iterrows():
        print(f"  最大分歧日 {d.date()}: 指数 {row['idx'] * 100:+.2f}% vs ETF {row['etf'] * 100:+.2f}%")
