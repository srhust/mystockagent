# -*- coding: utf-8 -*-
"""CycleSpec: 分行业产业周期规格(锚②的参数化, 替代统一"商品<60%")。
现状: 期货可用的行业用期货周期位置; 无可用数据源的行业标记 pending(不臆造)。
接入清单(待数据源): 动力煤价/港口库存(煤), 铜库存/升贴水(铜), 能繁母猪/养殖利润(猪),
DRAM现货/晶圆厂稼动率(存储), 运价指数(航运), 挖机销量(工程机械)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

# 行业 -> 产业驱动规格
SPECS = {
    "煤炭开采加工": dict(fut="JM0", indicator="焦煤期货位置(动力煤现货待接入)", direction="low_is_good", lag="0"),
    "工业金属": dict(fut="CU0", indicator="铜期货位置(库存/升贴水待接入)", direction="low_is_good", lag="0"),
    "养殖": dict(fut="LH0", indicator="生猪期货位置(2021上市,能繁母猪待接入)", direction="low_is_good", lag="0"),
    "贵金属": dict(fut="AU0", indicator="沪金位置(实际驱动为实际利率,慎用)", direction="low_is_good", lag="0"),
    "半导体": dict(fut=None, indicator="DRAM/NAND现货(待接入)", direction="low_is_good", lag="pending"),
    "航运港口": dict(fut=None, indicator="运价指数SCFI(待接入)", direction="low_is_good", lag="pending"),
    "工程机械": dict(fut=None, indicator="挖机销量(待接入)", direction="low_is_good", lag="pending"),
}
FUT_CACHE = {}
IND_CACHE = {}


def ingest(force: bool = False):
    """产业指标入库: 猪价/生猪供给/BDI -> data/ind_*.csv (缓存7天)。"""
    import time as _t
    import akshare as ak
    jobs = [("ind_hogprice", lambda: ak.futures_hog_core()),
            ("ind_hogsupply", lambda: ak.futures_hog_supply()),
            ("ind_bdi", lambda: ak.macro_shipping_bdi())]
    for tag, fn in jobs:
        fp = os.path.join(config.DATA_DIR, f"{tag}.csv")
        if not force and os.path.exists(fp) and _t.time() - os.path.getmtime(fp) < 7 * 86400:
            continue
        try:
            df = fn()
            if df is not None and len(df):
                df.to_csv(fp, index=False)
                print(f"[ingest] {tag}: {len(df)}行")
        except Exception as e:
            print(f"[ingest FAIL] {tag}: {type(e).__name__} {str(e)[:50]}")


def _series(tag: str):
    if tag not in IND_CACHE:
        fp = os.path.join(config.DATA_DIR, f"{tag}.csv")
        if not os.path.exists(fp):
            IND_CACHE[tag] = None
        else:
            df = pd.read_csv(fp)
            col = "value" if "value" in df.columns else df.columns[-1]
            s = pd.to_numeric(df[col], errors="coerce")
            s.index = pd.to_datetime(df["date"] if "date" in df.columns else df.iloc[:, 0])
            IND_CACHE[tag] = s.dropna().sort_index()
    return IND_CACHE[tag]


def series_position(tag: str, date, win: int = 1250):
    """序列在自身win点的分位(0=历史低,1=历史高)。"""
    s = _series(tag)
    if s is None:
        return None
    w = s[s.index <= pd.Timestamp(date)]
    if len(w) < 100:
        return None
    w = w.iloc[-win:]
    return float((w.iloc[-1] - w.min()) / (w.max() - w.min())) if w.max() > w.min() else None





def fut_position(fut: str, date, win: int = 1250):
    """期货在自身win日区间的位置(0=底,1=顶)。"""
    if fut not in FUT_CACHE:
        fp = os.path.join(config.DATA_DIR, f"fut_{fut}.csv")
        if not os.path.exists(fp):
            FUT_CACHE[fut] = None
        else:
            f = pd.read_csv(fp, parse_dates=["date"]).set_index("date")["close"].astype(float)
            FUT_CACHE[fut] = f
    f = FUT_CACHE.get(fut)
    if f is None:
        return None
    w = f[f.index <= pd.Timestamp(date)]
    if len(w) < 250:
        return None
    w = w.iloc[-win:]
    return float((w.iloc[-1] - w.min()) / (w.max() - w.min())) if w.max() > w.min() else None


def industry_cycle_state(sector: str, date, max_pos: float = 0.60):
    """返回 (ok, pos, note): ok=None=无数据源(不拦截); ok=True=产业位置支持买入。
    优先级: 产业现货序列(猪价/BDI) > 期货位置。"""
    spec = SPECS.get(sector)
    if spec is None:
        return None, None, "无规格"
    if sector == "养殖":
        pos = series_position("ind_hogprice", date)
        if pos is not None:
            return pos < max_pos, pos, "猪价历史分位"
    if sector == "航运港口":
        pos = series_position("ind_bdi", date)
        if pos is not None:
            return pos < max_pos, pos, "BDI分位"
    if spec.get("fut") is None:
        return None, None, spec["indicator"] + "(待接入)"
    pos = fut_position(spec["fut"], date)
    if pos is None:
        return None, None, "期货数据不足"
    return pos < max_pos, pos, spec["indicator"]
