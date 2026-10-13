# -*- coding: utf-8 -*-
"""产业指标数据源探测: 动力煤/能繁母猪/运价/DRAM/挖机 候选接口逐一试。"""
import sys

sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import akshare as ak


def probe(tag, fn):
    try:
        df = fn()
        print(f"[OK] {tag}: {type(df).__name__} rows={len(df)} cols={list(df.columns)[:6] if hasattr(df, 'columns') else '-'}")
        print(df.tail(2).to_string())
    except Exception as e:
        print(f"[FAIL] {tag}: {type(e).__name__} {str(e)[:60]}")


probe("动力煤期货ZC0", lambda: ak.futures_main_sina(symbol="ZC0", start_date="20180101", end_date="20261231"))
probe("秦皇岛动力煤(Q5500)", lambda: ak.spot_price_qh(symbol="综合交易价", period="周"))
try:
    import akshare as ak2
    cands = [n for n in dir(ak2) if any(k in n.lower() for k in ["bdi", "shipping", "coal", "pig", "hog", "sow", "dram", "excavat", "spot_price", "energy"])]
    print("候选接口:", cands[:30])
except Exception as e:
    print("枚举失败", e)
