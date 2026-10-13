# -*- coding: utf-8 -*-
"""EM 伪装浏览器单发修复: 完整头 + 慢节奏 + 重试。参数: probe(前5只) 或 all。"""
import json
import os
import sys
import time

import pandas as pd
import requests

DATA = r"E:\mystockagent\data"
MODE = sys.argv[1] if len(sys.argv) > 1 else "probe"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
     "Referer": "https://quote.eastmoney.com/", "Accept": "*/*",
     "Accept-Language": "zh-CN,zh;q=0.9", "Connection": "keep-alive"}
S = requests.Session()
S.headers.update(H)
files = sorted(f for f in os.listdir(DATA) if f.startswith("stock_") and f.endswith(".csv"))
files = files[:5] if MODE == "probe" else files
ok = fail = 0
for i, fn in enumerate(files):
    code = fn[6:-4]
    mkt = "1" if code.startswith(("6", "9", "5")) else "0"
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={mkt}.{code}"
           f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f53&klt=101&fqt=1&beg=20100101&end=20261231")
    kl = None
    for k in range(3):
        try:
            j = json.loads(S.get(url, timeout=10).text)
            kl = j.get("data", {}).get("klines")
            if kl:
                break
        except Exception as e:
            if k == 2:
                print(f"FAIL {code}: {type(e).__name__} {str(e)[:40]}", flush=True)
        time.sleep(2.5)
    if not kl:
        fail += 1
        continue
    rows = [x.split(",") for x in kl]
    df = pd.DataFrame({"date": pd.to_datetime([r[0] for r in rows]),
                       "close": pd.to_numeric([r[1] for r in rows], errors="coerce")}).dropna()
    r = df["close"].pct_change()
    df = df[(r.abs().fillna(0) <= 0.25) | (df.index == 0)].reset_index(drop=True)
    df.to_csv(os.path.join(DATA, fn), index=False)
    ok += 1
    if i % 20 == 0:
        print(f"[{i}/{len(files)}] ok={ok} fail={fail}", flush=True)
    time.sleep(2.2)
print(f"[done {MODE}] ok={ok} fail={fail}", flush=True)
