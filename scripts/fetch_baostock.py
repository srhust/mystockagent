# -*- coding: utf-8 -*-
"""baostock 全量抓取: 247只 qfq 单锚点全历史 (机器专用接口, 无验证码)。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import baostock as bs  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "all"
lg = bs.login()
print("[login]", lg.error_code, lg.error_msg, flush=True)
files = sorted(f for f in os.listdir(config.DATA_DIR) if f.startswith("stock_") and f.endswith(".csv"))
if MODE == "probe":
    files = files[:5]
ok = fail = 0
for i, fn in enumerate(files):
    code = fn[6:-4]
    mkt = "sh" if code.startswith(("6", "5", "9")) else "sz"
    rs = bs.query_history_k_data_plus(f"{mkt}.{code}", "date,close", start_date="2010-01-01",
                                      end_date="2026-12-31", frequency="d", adjustflag="2")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if len(rows) < 100:
        fail += 1
        print(f"FAIL {code}: {rs.error_code} {rs.error_msg}", flush=True)
        continue
    df = pd.DataFrame(rows, columns=["date", "close"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna().drop_duplicates("date").sort_values("date")
    r = df["close"].pct_change()
    df = df[(r.abs().fillna(0) <= 0.25) | (df.index == 0)]
    df.to_csv(os.path.join(config.DATA_DIR, fn), index=False)
    ok += 1
    if i % 20 == 0:
        print(f"[{i}/{len(files)}] ok={ok} fail={fail}", flush=True)
    time.sleep(0.25)
bs.logout()
print(f"[done] ok={ok} fail={fail}", flush=True)
