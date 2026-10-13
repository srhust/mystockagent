# -*- coding: utf-8 -*-
"""拉取同花顺行业列表 + 测试全区间行业指数。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["NO_PROXY"] = "*"

import _path  # noqa: F401
import akshare as ak  # noqa: E402

df = ak.stock_board_industry_name_ths()
out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "ths_industries.txt")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    f.write(f"共 {len(df)} 个同花顺行业:\n")
    for _, row in df.iterrows():
        f.write(f"  {row['name']}  ({row['code']})\n")
print("saved to", out)
