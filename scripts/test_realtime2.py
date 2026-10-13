# -*- coding: utf-8 -*-
"""测试行业级盘中实时行情候选接口。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

os.environ["NO_PROXY"] = "*"
import socket
socket.setdefaulttimeout(20)

import akshare as ak  # noqa: E402
import pandas as pd  # noqa: E402

pd.set_option("display.width", 200)

# 候选1: 同花顺行业板块汇总(可能含当日实时涨跌)
print("=== stock_board_industry_summary_ths ===")
try:
    df = ak.stock_board_industry_summary_ths()
    print("OK", len(df), "行, 列:", list(df.columns))
    print(df.head(3).to_string())
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:150])

# 候选2: 申万一级行业实时
print("\n=== index_realtime_sw ===")
try:
    df = ak.index_realtime_sw(symbol="市场表征")
    print("OK(市场表征)", len(df), "行, 列:", list(df.columns)[:10])
except Exception as e:
    print("FAIL(市场表征):", type(e).__name__, str(e)[:120])
try:
    df = ak.index_realtime_sw(symbol="一级行业")
    print("OK(一级行业)", len(df), "行, 列:", list(df.columns)[:10])
    print(df.head(5).to_string())
except Exception as e:
    print("FAIL(一级行业):", type(e).__name__, str(e)[:120])

# 候选3: 新浪指数实时(看有没有行业类指数)
print("\n=== stock_zh_index_spot_sina ===")
try:
    df = ak.stock_zh_index_spot_sina()
    print("OK", len(df), "行, 列:", list(df.columns))
    sw = df[df["名称"].astype(str).str.contains("行业|半导体|有色|煤炭", na=False)]
    print("含行业关键词的行数:", len(sw))
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:150])
