# -*- coding: utf-8 -*-
"""修复 11 个板块指数的整年缺口: 删缓存 → 逐年重抓 → 复检。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import data  # noqa: E402

# audit_gaps.py 发现的异常板块
BROKEN = ["元件", "化学原料", "化纤", "水泥建材", "油气开采", "煤炭", "玻璃玻纤",
          "石油加工", "证券", "贵金属", "造纸"]

print("=== 1. 删除污染缓存(按 THS 名) ===")
for name in BROKEN:
    ths = config.CYCLE_SECTORS[name]["ths"]
    for p in (os.path.join(config.DATA_DIR, f"ind_{name}.csv"),
              os.path.join(config.DATA_DIR, f"ind_{ths}.csv")):
        if os.path.exists(p):
            os.remove(p)
            print(f"  删除 {p}")

print("\n=== 2. 逐年重抓(每年3次重试) ===")
ok, still_bad = [], []
for name in BROKEN:
    ths = config.CYCLE_SECTORS[name]["ths"]
    df = data.get_industry_hist(ths)
    if df is None or len(df) < 100:
        still_bad.append((name, "抓取失败"))
        continue
    gaps = df["date"].diff().dt.days
    n_bad = int((gaps.fillna(0) > 40).sum())
    ok.append((name, len(df), n_bad))
    print(f"  {name:<6} {len(df):>5}行 {df['date'].min().date()}~{df['date'].max().date()} 缺口(>40天): {n_bad}")

print(f"\n=== 3. 结果: 成功 {len(ok)}/{len(BROKEN)}, 仍异常: {still_bad} ===")
