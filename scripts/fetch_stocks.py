# -*- coding: utf-8 -*-
"""抓取 meta 中全部个股的日线前复权历史(akshare EM源), 存 data/stock_<code>.csv。
增量: 文件存在且末尾在 7 天内 → 跳过。限速+重试。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import akshare as ak  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
pairs = []
for name, m in obj["meta"].items():
    for code, sname in m.get("stocks", []):
        pairs.append((str(code).zfill(6), sname, name))
pairs = sorted(set(pairs))
print(f"[plan] {len(pairs)} 只个股", flush=True)

today = pd.Timestamp.now().strftime("%Y%m%d")
ok = skip = fail = 0
fails = []
for i, (code, sname, sector) in enumerate(pairs):
    fp = os.path.join(config.DATA_DIR, f"stock_{code}.csv")
    if os.path.exists(fp):
        try:
            tail = pd.read_csv(fp, usecols=["date"]).iloc[-1, 0]
            if (pd.Timestamp.now() - pd.Timestamp(tail)).days <= 7:
                skip += 1
                continue
        except Exception:
            pass
    got = False
    for att in range(3):
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                    start_date="20100101", end_date=today, adjust="qfq")
            if df is not None and len(df):
                df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
                                        "最高": "high", "最低": "low", "成交量": "volume",
                                        "成交额": "amount"})
                df["date"] = df["date"].astype(str)
                df.to_csv(fp, index=False)
                ok += 1
                got = True
            break
        except Exception as e:
            time.sleep(2 + att * 3)
            if att == 2:
                fails.append(f"{code}({sname}): {str(e)[:60]}")
    if not got and code not in [f.split(":")[0] for f in fails]:
        fail += 1
    if (i + 1) % 25 == 0:
        print(f"[{i + 1}/{len(pairs)}] ok={ok} skip={skip} fail={fail}", flush=True)
    time.sleep(0.35)

print(f"[done] ok={ok} skip={skip} fail={fail}", flush=True)
for f in fails:
    print("  FAIL", f, flush=True)
