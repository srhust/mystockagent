# -*- coding: utf-8 -*-
"""盘点 akshare 1.19.1 中可用的实时/盘中接口，并实测关键候选。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

os.environ["NO_PROXY"] = "*"
import socket
socket.setdefaulttimeout(20)

import akshare as ak  # noqa: E402

print("akshare", ak.__version__)
print("\n--- 同花顺(ths)相关接口 ---")
ths = [x for x in dir(ak) if "ths" in x.lower()]
for x in sorted(ths):
    print("  ", x)

print("\n--- 实时/盘中关键词接口 ---")
kw = [x for x in dir(ak) if any(k in x.lower() for k in ("spot", "intraday", "realtime", "_min_", "minute"))]
for x in sorted(kw)[:60]:
    print("  ", x)

# 实测1: 同花顺行业指数当天盘中数据是否可从日K接口拿到(测试今天的日期)
print("\n--- 实测: THS 行业指数接口当日数据 ---")
try:
    df = ak.stock_board_industry_index_ths(
        symbol="半导体", start_date="20261009", end_date="20261012")
    print("OK 返回", len(df), "行")
    print(df.tail(3).to_string())
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:150])

# 实测2: 新浪全市场实时快照(免费替代方案)
print("\n--- 实测: 新浪全A实时快照 stock_zh_a_spot ---")
try:
    df = ak.stock_zh_a_spot()
    print("OK 返回", len(df), "行, 列:", list(df.columns)[:8])
    print(df.head(2).to_string())
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:150])
