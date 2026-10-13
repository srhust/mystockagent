# -*- coding: utf-8 -*-
"""产业指标探测第2轮: 猪价/能繁/BDI/现货煤价。"""
import sys

sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import akshare as ak


def probe(tag, fn):
    try:
        df = fn()
        print(f"[OK] {tag}: rows={len(df)} cols={list(df.columns)[:7]}")
        print(df.tail(2).to_string()[:300])
    except Exception as e:
        print(f"[FAIL] {tag}: {type(e).__name__} {str(e)[:70]}")


probe("生猪现货均价", lambda: ak.spot_hog_soozhu())
probe("生猪核心指标", lambda: ak.futures_hog_core())
probe("生猪供给(能繁?)", lambda: ak.futures_hog_supply())
probe("BDI运价", lambda: ak.macro_shipping_bdi())
probe("qh现货价格表", lambda: ak.spot_price_qh())
