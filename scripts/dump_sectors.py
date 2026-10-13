# -*- coding: utf-8 -*-
"""输出全部观察行业的配置与当前状态。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import strategy as strat  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]
print("SECTOR|CAT|THS|CYC|FUT|NSTOCK|TEMP|STAGE|POS")
for n in config.CYCLE_SECTORS:
    m = meta.get(n, {})
    df = dfs.get(n)
    if df is None:
        continue
    last = df.iloc[-1]
    p = strat.position_target(df, mode="cold").iloc[-1]
    print(f"{n}|{m.get('cat', '')}|{m.get('ths', '')}|{m.get('cycle_len', '')}|"
          f"{m.get('fut', '-')}|{len(m.get('stocks', []))}|{last['temp']:.0f}|{last['stage']}|{int(p)}")
