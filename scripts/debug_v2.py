# -*- coding: utf-8 -*-
"""对拍: 引擎 _cold_v2_position vs 研究模拟器 X4 规则(同一份 fut 列)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import strategy as strat  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]
bad = []
for name, df in dfs.items():
    pe = strat.position_target(df, mode="cold_v2")
    # 模拟器同规则: E0 入场 + X4 出场, 用同一份列
    t = df["temp"]
    fm = df.get("fut_mom20")
    fb = df.get("fut_below_ma60")
    if fm is None:
        ps = strat.position_target(df, mode="cold")
    else:
        m = fm.values.astype(float)
        b = fb.values.astype(bool)
        tv = t.values.astype(float)
        n = len(tv)
        arr = np.zeros(n, dtype=int)
        st = 0
        for i in range(n):
            ti, mi = tv[i], m[i]
            if np.isnan(ti):
                arr[i] = st
                continue
            if st == 1:
                if ti > 75 or (ti > 65 and np.isfinite(mi) and mi < 0 and b[i]):
                    st = 0
            else:
                if ti < 12:
                    st = 1
            arr[i] = st
        ps = pd.Series(arr, index=df.index)
    d = (pe.values != ps.values).sum()
    if d:
        idx = np.where(pe.values != ps.values)[0][:3]
        bad.append(f"{name}: {d} 天不一致 首处{[str(df.index[i].date()) for i in idx]} "
                   f"engine={[int(pe.values[i]) for i in idx]} sim={[int(ps.values[i]) for i in idx]}")
print("[diff] 不一致板块:" if bad else "[diff] 全部一致 — 差异来自数据版本(fut 重新抓取)")
for x in bad:
    print("  ", x)
