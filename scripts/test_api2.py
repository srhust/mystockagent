# -*- coding: utf-8 -*-
"""验证百度估值接口 + 东财接口（NO_PROXY 修复后）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["NO_PROXY"] = "*"  # 必须在 import akshare 前设置

import _path  # noqa: F401
import akshare as ak  # noqa: E402


def t(name, fn):
    try:
        df = fn()
        print(f"[OK] {name}: shape={getattr(df, 'shape', None)}")
        if df is not None and hasattr(df, "columns"):
            print("     cols:", list(df.columns))
            if len(df):
                print("     last:", df.iloc[-1].to_dict())
        return True
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {str(e)[:160]}")
        return False


t("行业板块列表", lambda: ak.stock_board_industry_name_em())
t("板块日K-煤炭", lambda: ak.stock_board_industry_hist_em(
    symbol="煤炭行业", start_date="20240101", end_date="20250630", period="日k", adjust="qfq"))
t("沪深300指数", lambda: ak.index_zh_a_hist(
    symbol="000300", period="daily", start_date="20240101", end_date="20250630"))
t("板块成分-煤炭", lambda: ak.stock_board_industry_cons_em(symbol="煤炭行业"))
time.sleep(1)
t("百度PB-中国神华", lambda: ak.stock_zh_valuation_baidu(
    symbol="601088", indicator="市净率", period="全部"))
time.sleep(1)
t("百度PE-牧原", lambda: ak.stock_zh_valuation_baidu(
    symbol="002714", indicator="市盈率(TTM)", period="全部"))
print("DONE")
