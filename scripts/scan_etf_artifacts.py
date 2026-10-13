# -*- coding: utf-8 -*-
"""扫描全部 ETF 的未复权伪影(单日|收益|>12%必为份额折算) + 测试分红接口。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import data  # noqa: E402

print("=== 单日 |收益| > 12% 事件（ETF有10%涨跌停板, 必为份额折算/合并伪影）===")
n_bad = 0
for sector, (sym, name, _n) in config.ETF_MAP.items():
    df = data.get_etf_hist(sym)
    if df is None or len(df) < 2:
        continue
    r = df["close"].pct_change()
    bad = df.loc[r.abs() > 0.12, ["date", "close"]]
    for _, row in bad.iterrows():
        i = df.index[df["date"] == row["date"]][0]
        prev = df["close"].iloc[i - 1]
        print(f"  {sector:<6} {sym} {name:<8} {row['date'].date()}: {prev:.3f} -> {row['close']:.3f} "
              f"({(row['close'] / prev - 1) * 100:+.1f}%)")
        n_bad += 1
print(f"共 {n_bad} 个伪影事件")

print("\n=== fund_etf_dividend_sina 分红历史测试 ===")
for sym in ("sh515220", "sh512200", "sh515210", "sh512000"):
    try:
        d = ak_div = None
        import akshare as ak
        d = ak.fund_etf_dividend_sina(symbol=sym)
        if d is None or len(d) == 0:
            print(f"  {sym}: 空")
        else:
            print(f"  {sym}: {len(d)}行 列{list(d.columns)}")
            print(d.tail(3).to_string())
    except Exception as e:  # noqa: BLE001
        print(f"  {sym}: FAIL {type(e).__name__} {str(e)[:80]}")
