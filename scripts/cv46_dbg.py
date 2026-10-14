# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import numpy as np
import pandas as pd
from cycle_portfolio import run_contract

n = 8
dts = pd.date_range("2022-01-03", periods=n, freq="B")
px = pd.Series(10.0, index=dts)
ma = pd.Series([1000.0, 1000.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0], index=dts)
snap = {(d.to_period("M"), "MU"): 1.0 for d in dts}
collect = {}
run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, {"MU": {1}}, {"MU": pd.Series(True, index=dts)},
             dts, {"MU": pd.Series(np.arange(n), index=dts)}, collect=collect, reserve_mode=True, hold_days=3)
print("FILLS:", collect["fills"])
print("SOURCES:", collect["sources_end"])
print("EQ:", [round(float(x), 2) for x in collect["eq"].values])
