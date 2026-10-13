# -*- coding: utf-8 -*-
"""检查 enriched.pkl 的结构（供审查 intraday.py 用）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

obj = pd.read_pickle(r"E:\mystockagent\data\enriched.pkl")
print("keys:", list(obj.keys()), "| ts:", obj["ts"])
df = obj["dfs"]["煤炭"]
print("煤炭 列:", list(df.columns))
print("最后2行:")
print(df[["close", "temp", "stage"]].tail(2).to_string())
print("index 类型:", type(df.index).__name__, "| 最后日期:", df.index[-1])
# 日K缓存列（intraday 需要对比单位）
from cycle_system import data  # noqa: E402
idx = data.get_industry_hist("煤炭")
print("\n煤炭日K列:", list(idx.columns))
print(idx.tail(2).to_string())
