# -*- coding: utf-8 -*-
"""审查十六轮: 统一数据拉取器v2 — 取代fetch_mu.py的qfq通道(akshare qfq层对美股系统性有毒)。
全部改用原始收盘+拆股缺口自检测复权(1.8<r<12整数倍判定), 逐标的验证(≤0/最差日)后落盘。
用法: python scripts/fetch_all_raw.py [SYM ...]  缺省刷新全部标的池。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import akshare as ak
import pandas as pd

import config

D = os.path.join(config.DATA_DIR, "mu")
POOL = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]


def fetch_raw(sym):
    df = ak.stock_us_daily(symbol=sym, adjust="")
    df = df.reset_index().rename(columns={"date": "date", "close": "close"})[["date", "close"]]
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] >= "2009-01-01"].sort_values("date").reset_index(drop=True)
    c = df["close"].astype(float)
    ratio = c.shift(1) / c
    cum, factors = 1.0, []
    for r in ratio:
        r = float(r) if pd.notna(r) else 1.0
        if 1.8 < r < 12:
            cum *= r
        factors.append(cum)
    c_adj = c * pd.Series(factors)
    out = pd.DataFrame({"date": df["date"], "close": c_adj.values})
    return out, c_adj


def main(syms):
    bad = []
    for sym in syms:
        try:
            out, c = fetch_raw(sym)
            r = c.pct_change()
            ok = (c <= 0).sum() == 0 and (len(r.dropna()) == 0 or r.min() > -0.5)
            if not ok:
                bad.append(sym)
                print(f"[BAD] {sym}: ≤0={int((c <= 0).sum())} 最差{r.min():.1%} — 不落盘")
                continue
            out.to_csv(os.path.join(D, f"{sym}.csv"), index=False)
            print(f"[OK] {sym}: {len(c)}行 {out['date'].iloc[0].date()}~{out['date'].iloc[-1].date()} 最差日{r.min():.1%}")
        except Exception as e:
            bad.append(sym)
            print(f"[FAIL] {sym}: {type(e).__name__} {str(e)[:50]}")
    print(f"\n完成 {len(syms) - len(bad)}/{len(syms)}" + (f"，失败: {bad}" if bad else ""))


if __name__ == "__main__":
    main(sys.argv[1:] if len(sys.argv) > 1 else POOL)
