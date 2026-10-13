# -*- coding: utf-8 -*-
"""精确定位：http vs https、编号子域名、UA 对东财接口的影响。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import requests  # noqa: E402

os.environ["NO_PROXY"] = "*"

PARAMS = {
    "pn": "1", "pz": "5", "po": "1", "np": "1",
    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    "fltt": "2", "invt": "2", "fid": "f3",
    "fs": "m:90 t:2 f:!50",
    "fields": "f12,f14",
}

s = requests.Session()
s.trust_env = False

tests = [
    ("https + 17.push2, no UA", "https://17.push2.eastmoney.com/api/qt/clist/get"),
    ("https + push2, no UA", "https://push2.eastmoney.com/api/qt/clist/get"),
    ("http + 17.push2, no UA", "http://17.push2.eastmoney.com/api/qt/clist/get"),
    ("https + 17.push2, UA", "https://17.push2.eastmoney.com/api/qt/clist/get"),
    ("https + push2his, no UA", "https://push2his.eastmoney.com/api/qt/stock/kline/get"),
]
for name, url in tests:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"} if "UA" in name else {}
    try:
        r = s.get(url, params=PARAMS, headers=headers, timeout=10)
        print(f"[OK] {name}: {r.status_code} {r.text[:60]}")
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__} {str(e)[:100]}")

# 检查 akshare 其他模块用的域名
import akshare  # noqa: E402
from akshare.stock_feature import stock_board_industry_em as mod
import inspect
src = inspect.getsource(mod.stock_board_industry_hist_em)
print("\n== stock_board_industry_hist_em 中的 URL ==")
for line in src.splitlines():
    if "http" in line:
        print("   ", line.strip())
