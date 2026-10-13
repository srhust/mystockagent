# -*- coding: utf-8 -*-
"""找出缓存缺失的股票/PB，并单独重试抓取。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import config  # noqa: E402
from cycle_system import data  # noqa: E402

missing_stk, missing_pb = [], []
for name, meta in config.CYCLE_SECTORS.items():
    for code, cname in meta["stocks"]:
        p1 = os.path.join(config.DATA_DIR, f"stk_{code}.csv")
        p2 = os.path.join(config.DATA_DIR, f"pb_{code}.csv")
        if not os.path.exists(p1):
            missing_stk.append((code, cname, name))
        if not os.path.exists(p2):
            missing_pb.append((code, cname, name))

print("缺失个股行情:", missing_stk)
print("缺失个股PB:", missing_pb)

for code, cname, sec in missing_stk:
    print(f"重试 {cname}({code}) 行情 ...")
    df = data.get_stock_hist(code)
    print("  ->", "OK" if df is not None else "FAIL", None if df is None else len(df))
    time.sleep(1)

for code, cname, sec in missing_pb:
    print(f"重试 {cname}({code}) PB ...")
    df = data.get_stock_valuation(code, "市净率")
    print("  ->", "OK" if df is not None else "FAIL", None if df is None else len(df))
    time.sleep(1)
