# -*- coding: utf-8 -*-
"""验证：THS 行业指数历史深度 + 百度估值历史长度。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["NO_PROXY"] = "*"

import _path  # noqa: F401
import akshare as ak  # noqa: E402

out = []


def log(s):
    print(s)
    out.append(s)


try:
    df = ak.stock_board_industry_index_ths(symbol="煤炭开采加工", start_date="20100101", end_date="20261231")
    log(f"[THS 煤炭开采加工 指数] shape={df.shape}")
    log(f"  cols={list(df.columns)}")
    log(f"  首行: {df.iloc[0].to_dict()}")
    log(f"  末行: {df.iloc[-1].to_dict()}")
except Exception as e:
    log(f"[THS 指数 FAIL] {type(e).__name__}: {str(e)[:150]}")

time.sleep(2)
try:
    df = ak.stock_zh_valuation_baidu(symbol="601088", indicator="市净率", period="近十年")
    log(f"[百度PB 神华 近十年] shape={df.shape}")
    log(f"  首行: {df.iloc[0].to_dict()}")
    log(f"  末行: {df.iloc[-1].to_dict()}")
except Exception as e:
    log(f"[百度PB FAIL] {type(e).__name__}: {str(e)[:150]}")

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "diag_depth.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
