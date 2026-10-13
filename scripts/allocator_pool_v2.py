# -*- coding: utf-8 -*-
"""审查十六轮: 分配器评分池扩展 — 标的池 MU/WDC/TSM/AMAT/LRCX/KLAC/ASML/TER/FCX/CAT。
本地数据体检(≤0/最差日), 毒则akshare原始+拆股缺口自复权重取; 统一S2双路径冻结规则评分=终值/|MDD|。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import akshare as ak
import numpy as np
import pandas as pd

import config

TICKERS = {"MU": "MU", "WDC": "WDC", "TSM": "TSM", "AMAT": "AMAT", "LRCX": "LRCX",
           "KLAC": "KLAC", "ASML": "ASML", "TER": "TER", "FCX": "FCX", "CAT": "CAT"}


def load_raw(sym):
    df = ak.stock_us_daily(symbol=sym, adjust="")
    df = df.reset_index().rename(columns={"date": "date", "close": "close"})[["date", "close"]]
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] >= "2009-01-01"].sort_values("date").reset_index(drop=True)
    c = df["close"].astype(float)
    ratio = c.shift(1) / c
    cum = 1.0
    factors = []
    for r in ratio:
        r = float(r) if pd.notna(r) else 1.0
        if 1.8 < r < 12:
            cum *= r
        factors.append(cum)
    c_adj = c * pd.Series(factors)
    return pd.DataFrame({"date": df["date"], "close": c_adj.values})


def get_clean(sym, fname):
    fp = os.path.join(config.DATA_DIR, "mu", fname)
    if os.path.exists(fp):
        c = pd.read_csv(fp, parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
        r = c.pct_change()
        if (c <= 0).sum() == 0 and len(c) > 1500 and (not len(r.dropna()) or r.min() > -0.5):
            return c, "本地OK"
    try:
        df = load_raw(sym)
        c = df.set_index("date")["close"]
        r = c.pct_change()
        if (c <= 0).sum() == 0 and r.min() > -0.5:
            df.to_csv(fp, index=False)
            return c, "原始重取+自复权"
        return c, f"仍毒(≤0:{int((c <= 0).sum())})"
    except Exception as e:
        return None, f"取数失败:{type(e).__name__}"


def s2_score(c):
    f = build_features(c.reset_index(drop=True).set_axis(c.index), [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    idx = c.index
    ev = (((c.values > ma20.values) & (ma20.values > np.roll(ma20.values, 5)) & state250 & (dd.values <= -0.40) & (c.values <= ma50.values * 1.05))
          | ((ma50.values > np.roll(ma50.values, 10)) & (dd.values >= -0.30) & (dd.values <= -0.10)))
    ev[~np.isfinite(ev)] = False
    pos = pd.Series(ev.astype(float), index=idx).rolling(120, min_periods=1).max().astype(float).shift(1).fillna(0.0)
    ret = c.pct_change().fillna(0.0)
    eq = 100.0 * (1 + pos * ret).cumprod()
    mdd = float((eq / eq.cummax() - 1).min())
    return float(eq.iloc[-1]), mdd


sys.path.insert(0, r"E:\mystockagent\cycle_system")
from bottom_nowcaster import build_features  # noqa: E402

rows = []
for sym, fname in TICKERS.items():
    c, how = get_clean(sym, fname + ".csv")
    if c is None:
        rows.append(dict(sym=sym, src=how, n=0, final=np.nan, mdd=np.nan, score=np.nan))
        print(f"{sym}: {how}")
        continue
    fin, mdd = s2_score(c)
    sc = fin / abs(mdd) if mdd else np.inf
    rows.append(dict(sym=sym, src=how, n=len(c), final=round(fin, 1), mdd=f"{mdd:.0%}", score=round(sc)))
    print(f"{sym}: {how} n={len(c)} S2终值{fin:.1f} MDD{mdd:.0%} 评分{sc:.0f}")
d = pd.DataFrame(rows).dropna().sort_values("score", ascending=False)
d.to_csv(os.path.join(config.DATA_DIR, "allocator_pool_scores.csv"), index=False)
print("\n评分榜(已存 data/allocator_pool_scores.csv):")
print(d[["sym", "src", "final", "mdd", "score"]].to_string(index=False))
