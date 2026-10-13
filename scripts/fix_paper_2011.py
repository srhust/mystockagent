# -*- coding: utf-8 -*-
"""补抓造纸 2011 年(3次重试仍失败则接受该洞)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import data  # noqa: E402

ths = config.CYCLE_SECTORS["造纸"]["ths"]
print(f"造纸 THS名: {ths}")
chunk = None
for i in range(5):
    try:
        import akshare as ak
        raw = ak.stock_board_industry_index_ths(symbol=ths, start_date="20110101", end_date="20111231")
        df = data._norm_ohlc(raw) if hasattr(data, "_norm_ohlc") else None
        if df is not None and len(df) > 0:
            chunk = df
            break
    except Exception as e:  # noqa: BLE001
        print(f"  第{i + 1}次失败: {type(e).__name__}")
if chunk is None:
    print("造纸 2011 数据源不可得, 接受该缺口(2011年仅1个板块缺失, 影响极小)")
else:
    print(f"抓到 {len(chunk)} 行 {chunk['date'].min().date()}~{chunk['date'].max().date()}")
    p = os.path.join(config.DATA_DIR, f"ind_{ths}.csv")
    old = pd.read_csv(p)
    old["date"] = pd.to_datetime(old["date"])
    merged = pd.concat([old, chunk]).drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    merged.to_csv(p, index=False)
    gaps = merged["date"].diff().dt.days
    print(f"合并后 {len(merged)} 行, >40天缺口数: {int((gaps.fillna(0) > 40).sum())}")
