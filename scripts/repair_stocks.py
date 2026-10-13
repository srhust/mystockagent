# -*- coding: utf-8 -*-
"""个股qfq修复: 全量重抓(单一复权锚点)替换分段漂移的缓存。"""
import os
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import akshare as ak  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402

warnings.filterwarnings("ignore")
files = sorted(f for f in os.listdir(config.DATA_DIR) if f.startswith("stock_") and f.endswith(".csv"))
print(f"[repair] {len(files)} 只个股全量重抓(EM qfq, 单锚点)", flush=True)
ok = fail = 0
for i, fn in enumerate(files):
    code = fn[6:-4]
    fp = os.path.join(config.DATA_DIR, fn)
    df = None
    for k in range(3):
        try:
            sym = ("sh" if code.startswith(("6", "5", "9")) else "sz") + code
            raw = ak.stock_zh_a_daily(symbol=sym, adjust="qfq")
            df = pd.DataFrame({"date": pd.to_datetime(raw["date"]),
                               "close": pd.to_numeric(raw["close"], errors="coerce")})
            break
        except Exception as e:
            if k == 2:
                print(f"FAIL {code}: {type(e).__name__} {str(e)[:40]}", flush=True)
            time.sleep(2.5)
    if df is None or len(df) < 100:
        fail += 1
        continue
    df = df.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)
    r = df["close"].pct_change()
    bad = (r.abs() > 0.25) & (df["date"] != df["date"].iloc[0])  # A股涨跌停制度: >25%必为数据错误
    if bad.any():
        df = df[~bad.bfill().fillna(False) & ~bad.fillna(False)].reset_index(drop=True)
    df.to_csv(fp, index=False)
    ok += 1
    if i % 20 == 0:
        print(f"[{i}/{len(files)}] ok={ok} fail={fail}", flush=True)
    time.sleep(1.8)
print(f"[repair done] ok={ok} fail={fail}", flush=True)
