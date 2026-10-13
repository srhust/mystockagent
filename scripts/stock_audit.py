# -*- coding: utf-8 -*-
"""个股(EM)全量核对 + 期货全历史比对。需代理: HTTPS_PROXY。"""
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
END = "20261012"
START = (pd.Timestamp.now() - pd.Timedelta(days=400)).strftime("%Y%m%d")
L = [f"# 个股(EM)核对 + 期货全历史比对 {pd.Timestamp.now():%Y-%m-%d %H:%M}", ""]


def pct_diff(a, b, n_tail=250):
    j = a.index.intersection(b.index)
    if len(j) < 100:
        return None, len(j)
    tail = j[-n_tail:]
    d = (a.loc[tail].pct_change() - b.loc[tail].pct_change()).abs().dropna()
    big = int((d > 0.01).sum())
    px = float((a.loc[tail] / b.loc[tail] - 1).abs().max())
    return (big, px), len(j)


# ---- 1. 个股全量 ----
files = sorted(f for f in os.listdir(config.DATA_DIR) if f.startswith("stock_") and f.endswith(".csv"))
ok = mm = ff = 0
rows = []
for i, fn in enumerate(files):
    code = fn[6:-4]
    cache = pd.read_csv(os.path.join(config.DATA_DIR, fn), parse_dates=["date"])
    cache_s = pd.Series(cache["close"].values, index=pd.to_datetime(cache["date"])).dropna()
    try:
        raw = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=START,
                                 end_date=END, adjust="qfq")
        fresh = pd.Series(pd.to_numeric(raw["收盘"], errors="coerce").values,
                          index=pd.to_datetime(raw["日期"])).dropna()
        stat, nj = pct_diff(cache_s, fresh)
        if stat is None:
            ff += 1
            rows.append(f"- {code}: 交集不足({nj}天)")
        elif stat[0] <= 2 and stat[1] < 0.02:
            ok += 1
        else:
            mm += 1
            rows.append(f"- **{code}**: 偏差max={stat[1] * 100:.2f}% 收益差>1%天数={stat[0]}")
    except Exception as e:
        ff += 1
        rows.append(f"- {code}: 重取失败 {type(e).__name__} {str(e)[:40]}")
    if i % 50 == 0:
        print(f"[{i}/{len(files)}] ok={ok} mm={mm} ff={ff}", flush=True)
    time.sleep(0.15)
L.append(f"## 个股(EM, {len(files)}只): OK {ok} / MISMATCH {mm} / 失败 {ff}")
L.extend(rows if mm + ff else ["- 全部一致（>98%收盘偏差<2%且无收益级差异）"])

# ---- 2. 期货全历史 ----
L.append("\n## 期货全历史比对(缓存 vs 新浪重取)")
futs = ["JM0", "CU0", "LC0", "AU0", "SC0", "RB0", "FG0", "LH0", "M0", "SA0", "SP0", "TA0"]
for code in futs:
    fp = os.path.join(config.DATA_DIR, f"fut_{code}.csv")
    cache = pd.read_csv(fp, parse_dates=["date"])
    cache_s = pd.Series(cache["close"].values, index=pd.to_datetime(cache["date"])).dropna()
    try:
        raw = ak.futures_main_sina(symbol=code, start_date="20100101", end_date=END)
        fresh = pd.Series(pd.to_numeric(raw["收盘价"], errors="coerce").values,
                          index=pd.to_datetime(raw["日期"])).dropna()
        stat, nj = pct_diff(cache_s, fresh, n_tail=99999)
        if stat is None:
            L.append(f"- {code}: 交集不足({nj})")
        else:
            tag = "OK" if stat[0] <= 5 and stat[1] < 0.02 else "**MISMATCH**"
            L.append(f"- {code}: {tag} 全历史{nj}天 偏差max={stat[1] * 100:.2f}% 收益差>1%天数={stat[0]}")
        time.sleep(0.5)
    except Exception as e:
        L.append(f"- {code}: 重取失败 {type(e).__name__} {str(e)[:40]}")

with open(os.path.join(config.OUTPUT_DIR, "stock_audit.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("DONE", flush=True)
