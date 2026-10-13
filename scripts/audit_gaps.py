# -*- coding: utf-8 -*-
"""数据质量巡检: 全部板块指数的异常缺口(>10天且非节假日)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402

# 已知节假日窗口(春节/国庆/元旦): 15天以内的缺口且落在1-2月/9-10月/12-1月的视为正常
KNOWN_HOLIDAY_RANGES = [
    ("01-01", "02-28"), ("09-15", "10-15"), ("12-20", "01-05"),
]

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]
print(f"{'板块':<8}{'总行数':>6}{'首日':>12}{'末日':>12}{'异常缺口':>6}  明细")
n_bad = 0
for name in sorted(dfs):
    idx = dfs[name]["close"]
    gaps = idx.index.to_series().diff().dt.days
    bad = []
    for d, g in gaps[gaps > 15].items():
        start = d - pd.Timedelta(days=int(g))
        # 落在已知节假日的跳过
        md = f"{start.month:02d}-{start.day:02d}"
        if any(a <= md <= b for a, b in KNOWN_HOLIDAY_RANGES):
            continue
        bad.append(f"{start.date()}~{d.date()}({int(g)}天)")
    flag = "  <-- 异常!" if bad else ""
    if bad:
        n_bad += 1
    print(f"{name:<8}{len(idx):>6}{str(idx.index[0].date()):>12}{str(idx.index[-1].date()):>12}"
          f"{len(bad):>6}  {'; '.join(bad) if bad else '-'}{flag}")
print(f"\n异常板块数: {n_bad}/{len(dfs)}")
