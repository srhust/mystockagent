# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: F401

fp = r"E:\mystockagent\data\stock_601088.csv"
df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
df = df[df["close"] > 0].reset_index(drop=True)
r = df["close"].pct_change()
df = df[(r.abs().fillna(0) <= 0.25) | (df.index == 0)].reset_index(drop=True)
df.to_csv(fp, index=False)
print("601088 rows", len(df), "max1d", f"{df['close'].pct_change().abs().max() * 100:.1f}%")
