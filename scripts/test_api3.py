# -*- coding: utf-8 -*-
"""测试替代数据源：新浪、同花顺、腾讯（东财被限流时备用）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["NO_PROXY"] = "*"

import _path  # noqa: F401
import akshare as ak  # noqa: E402


def t(name, fn):
    try:
        df = fn()
        print(f"[OK] {name}: shape={getattr(df, 'shape', None)}")
        if df is not None and hasattr(df, "columns") and len(df):
            print("     cols:", list(df.columns)[:10])
            print("     last:", {k: df.iloc[-1][k] for k in list(df.columns)[:5]})
        return True
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {str(e)[:130]}")
        return False


t("新浪-沪深300指数", lambda: ak.stock_zh_index_daily(symbol="sh000300"))
t("新浪-个股日K牧原", lambda: ak.stock_zh_a_daily(symbol="sz002714", adjust="qfq"))
time.sleep(1)
t("同花顺-行业一览", lambda: ak.stock_board_industry_summary_ths())
time.sleep(1)
t("同花顺-半导体行业指数", lambda: ak.stock_board_industry_index_ths(symbol="半导体"))
time.sleep(1)
t("同花顺-行业成分(半导体)", lambda: ak.stock_board_industry_cons_ths(symbol="半导体"))
time.sleep(1)
t("东财重试-板块日K煤炭", lambda: ak.stock_board_industry_hist_em(
    symbol="煤炭行业", start_date="20240101", end_date="20250630", period="日k", adjust="qfq"))
print("DONE")
