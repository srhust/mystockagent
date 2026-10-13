# -*- coding: utf-8 -*-
"""验证 THS 源头是否有缺年数据。"""
import sys

sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import akshare as ak  # noqa: E402

for sym, yr in [("煤炭开采加工", "2023"), ("化学原料", "2022"), ("化学原料", "2021")]:
    try:
        df = ak.stock_board_industry_index_ths(symbol=sym, start_date=f"{yr}0101", end_date=f"{yr}1231")
        if df is None or len(df) == 0:
            print(f"{sym} {yr}: 空")
        else:
            dcol = [c for c in df.columns if "日" in c][0]
            d0, d1 = df[dcol].min(), df[dcol].max()
            print(f"{sym} {yr}: {len(df)}行 {d0}~{d1}")
    except Exception as e:  # noqa: BLE001
        print(f"{sym} {yr}: FAIL {type(e).__name__} {str(e)[:80]}")
