# -*- coding: utf-8 -*-
"""离线拼接修复: 段边界伪影检测(隔夜跳变>25%且板块平静) + 段缩放拼接。"""
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402

warnings.filterwarnings("ignore")
obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]
sec_ret = {n: d["close"].pct_change() for n, d in dfs.items()}
code2sec = {}
for n, m in meta.items():
    for c, _ in m.get("stocks", []):
        code2sec[str(c).zfill(6)] = n

files = sorted(f for f in os.listdir(config.DATA_DIR) if f.startswith("stock_") and f.endswith(".csv"))
fixed = clean = fail = 0
for fn in files:
    code = fn[6:-4]
    fp = os.path.join(config.DATA_DIR, fn)
    df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    df = df[df["close"] > 0].reset_index(drop=True)
    c = df["close"].astype(float)
    r = c.pct_change()
    bad_idx = df.index[(r.abs() > 0.25) & (df.index > 0)].tolist()
    if not bad_idx:
        clean += 1
        continue
    sname = code2sec.get(code)
    # 分辨真伪: 同日板块指数平静(|收益|<10%)才判伪影
    art = []
    for j in bad_idx:
        d0 = df["date"].iloc[j]
        sr = sec_ret.get(sname)
        sval = abs(float(sr.asof(d0))) if sr is not None and pd.notna(sr.asof(d0)) else 0.0
        if sval < 0.10:
            art.append(j)
    if not art:
        clean += 1
        continue
    # 从后往前缩放前段(后段为正确锚)
    for j in art:
        prev = float(c.iloc[j - 1])
        sr = sec_ret.get(sname)
        sval = float(sr.asof(df["date"].iloc[j])) if sr is not None and pd.notna(sr.asof(df["date"].iloc[j])) else 0.0
        target_prev = float(c.iloc[j]) / (1 + max(-0.10, min(0.10, sval)))
        k = target_prev / prev
        if 0.2 < k < 5.0:
            c.iloc[:j] = (c.iloc[:j] * k).values
    df["close"] = c
    df.to_csv(fp, index=False)
    fixed += 1
print(f"[offline repair] 修复{fixed}只 / 本就干净{clean}只 / 总{len(files)}只", flush=True)
# 全局复检
worst, wname = 0.0, ""
for fn in files:
    d = pd.read_csv(os.path.join(config.DATA_DIR, fn))
    if len(d) < 2:
        continue
    m = float(d["close"].pct_change().abs().max())
    if m > worst:
        worst, wname = m, fn
print(f"[check] 全库最大单日跳变: {worst * 100:.1f}% ({wname})", flush=True)
