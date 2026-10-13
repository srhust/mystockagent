# -*- coding: utf-8 -*-
"""baostock 全市场抓取: 含退市股, 全字段qfq, 3进程并行, 断点续跑。"""
import os
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import baostock as bs  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402

OUT = os.path.join(config.DATA_DIR, "bs_all")
os.makedirs(OUT, exist_ok=True)
FIELDS = "date,open,high,low,close,volume,amount,turn"
CODES_FILE = os.path.join(OUT, "_codes.txt")


def build_list():
    if os.path.exists(CODES_FILE):
        return [x.strip() for x in open(CODES_FILE) if x.strip()]
    lg = bs.login()
    basics = []
    rs = bs.query_stock_basic()
    while rs.error_code == "0" and rs.next():
        basics.append(rs.get_row_data())
    bs.logout()
    cols = ["code", "code_name", "ipoDate", "outDate", "type", "status"]
    df = pd.DataFrame(basics, columns=cols)
    df = df[(df["type"] == "1") & (df["code"].str.match(r"(sh|sz)\.6|sz\.0|sz\.3"))]
    codes = sorted(df["code"].tolist())
    with open(CODES_FILE, "w") as f:
        f.write("\n".join(codes))
    print(f"[universe] {len(codes)} 只(含退市)", flush=True)
    return codes


def fetch_one(code):
    fp = os.path.join(OUT, code.replace(".", "_") + ".csv")
    if os.path.exists(fp) and os.path.getsize(fp) > 3000:
        return ("skip", code)
    lg = bs.login()
    if lg.error_code != "0":
        return ("login-fail", code)
    try:
        rs = bs.query_history_k_data_plus(code, FIELDS, start_date="2006-01-01",
                                          end_date="2026-12-31", frequency="d", adjustflag="2")
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if len(rows) < 50:
            return ("empty", code)
        df = pd.DataFrame(rows, columns=FIELDS.split(","))
        for c in df.columns[1:]:
            df[c] = pd.to_numeric(df[c].replace("", np.nan) if False else df[c], errors="coerce")
        df = df.dropna(subset=["close"]).drop_duplicates("date").sort_values("date")
        df.to_csv(fp, index=False)
        return ("ok", code)
    except Exception as e:
        return (f"err:{type(e).__name__}", code)
    finally:
        bs.logout()


if __name__ == "__main__":
    import numpy as np  # noqa: F401
    codes = build_list()
    todo = [c for c in codes if not os.path.exists(os.path.join(OUT, c.replace(".", "_") + ".csv"))
            or os.path.getsize(os.path.join(OUT, c.replace(".", "_") + ".csv")) <= 3000]
    print(f"[start] 全市场 {len(codes)} 只, 待抓 {len(todo)}", flush=True)
    t0 = time.time()
    stat = {}
    with Pool(3) as p:
        for n, (st, code) in enumerate(p.imap_unordered(fetch_one, todo, chunksize=1), 1):
            stat[st] = stat.get(st, 0) + 1
            if n % 100 == 0:
                el = time.time() - t0
                eta = el / n * (len(todo) - n) / 3600
                print(f"[{n}/{len(todo)}] {stat} 已用{el / 60:.0f}min 预计剩余{eta:.1f}h", flush=True)
    print(f"[done] {stat} 总耗时{(time.time() - t0) / 3600:.1f}h", flush=True)
