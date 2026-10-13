# -*- coding: utf-8 -*-
"""验证 akshare 各数据接口可用性。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401  (把 .libs 加入 sys.path)

os.environ["NO_PROXY"] = "*"  # 绕过失效系统代理

import akshare as ak  # noqa: E402


def t(name, fn):
    try:
        df = fn()
        print(f"[OK] {name}: shape={getattr(df, 'shape', None)}")
        if df is not None and hasattr(df, "columns"):
            print("     cols:", list(df.columns)[:12])
            print("     head:", df.head(2).to_dict("records")[:2])
        return True
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {str(e)[:150]}")
        return False


t("行业板块列表", lambda: ak.stock_board_industry_name_em())
t("板块日K-煤炭", lambda: ak.stock_board_industry_hist_em(
    symbol="煤炭行业", start_date="20240101", end_date="20250630", period="日k", adjust="qfq"))
t("沪深300指数", lambda: ak.index_zh_a_hist(
    symbol="000300", period="daily", start_date="20240101", end_date="20250630"))
t("板块成分-煤炭", lambda: ak.stock_board_industry_cons_em(symbol="煤炭行业"))
t("个股PB-中国神华", lambda: ak.stock_a_indicator_lg(symbol="601088"))
t("期货-沪铜", lambda: ak.futures_main_sina(symbol="CU0", start_date="20240101", end_date="20250630"))
t("美股-美光", lambda: ak.stock_us_daily(symbol="MU", adjust="qfq"))
t("个股日K-牧原", lambda: ak.stock_zh_a_hist(
    symbol="002714", period="daily", start_date="20240101", end_date="20250630", adjust="qfq"))
print("DONE")
