# -*- coding: utf-8 -*-
"""Phase1/MU: 拉取MU历史日线(前复权)+基准(SPY/SOX), 存data/mu/。"""
import os
import sys
import time as _t

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import pandas as pd
import akshare as ak

import config

D = os.path.join(config.DATA_DIR, "mu")
os.makedirs(D, exist_ok=True)


def fetch_us(symbol, name, adj="qfq"):
    fp = os.path.join(D, f"{name}.csv")
    if os.path.exists(fp) and _t.time() - os.path.getmtime(fp) < 7 * 86400:
        print(f"[cache] {name}")
        return
    df = ak.stock_us_hist(symbol=symbol, period="daily", start_date="19950101",
                          end_date="20261231", adjust=adj)
    df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
                            "最低": "low", "成交量": "volume"})
    df.to_csv(fp, index=False)
    print(f"[ok] {name}: {len(df)}行 {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")


fetch_us("105.MU", "MU")          # 美光(新浪代码105.MU)
fetch_us("105.SPY", "SPY")        # 标普500 ETF
fetch_us("106.SOX", "SOX", adj="")  # 费城半导体指数(新浪美股代码尝试)
