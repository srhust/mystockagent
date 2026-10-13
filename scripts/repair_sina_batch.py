# -*- coding: utf-8 -*-
"""新浪qfq修复(分批+断点): 完成的记入done清单; 触发拼图验证时等待重试, 5次仍拦则存档退出。"""
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
DONE_FP = os.path.join(config.DATA_DIR, "repair_done.txt")
done = set(open(DONE_FP).read().split()) if os.path.exists(DONE_FP) else []
files = [f for f in sorted(os.listdir(config.DATA_DIR))
         if f.startswith("stock_") and f.endswith(".csv") and f[6:-4] not in done]
print(f"[resume] 待修复 {len(files)} / 247", flush=True)
ok = 0
with open(DONE_FP, "a") as dfp:
    for i, fn in enumerate(files):
        code = fn[6:-4]
        sym = ("sh" if code.startswith(("6", "5", "9")) else "sz") + code
        df = None
        for k in range(6):
            try:
                raw = ak.stock_zh_a_daily(symbol=sym, adjust="qfq")
                if raw is None or len(raw) < 100:
                    raise ValueError("empty")
                df = pd.DataFrame({"date": pd.to_datetime(raw["date"]),
                                   "close": pd.to_numeric(raw["close"], errors="coerce")}).dropna()
                break
            except Exception as e:
                msg = str(e)[:30]
                if k == 5:
                    print(f"FAIL {code}: {type(e).__name__} {msg}", flush=True)
                else:
                    print(f"[captcha? {code}] 等待90s后重试({k + 1}/6) — 请在浏览器过验证", flush=True)
                    time.sleep(90)
        if df is None:
            continue
        df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
        r = df["close"].pct_change()
        df = df[(r.abs().fillna(0) <= 0.25) | (df.index == 0)].reset_index(drop=True)
        df.to_csv(os.path.join(config.DATA_DIR, fn), index=False)
        dfp.write(code + "\n")
        dfp.flush()
        ok += 1
        if i % 10 == 0:
            print(f"[{i}/{len(files)}] 本轮ok={ok}", flush=True)
        time.sleep(3)
print(f"[done] 本轮修复 {ok}, 剩余 {len(files) - ok} — 重跑本脚本继续", flush=True)
