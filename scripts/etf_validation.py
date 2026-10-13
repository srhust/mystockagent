# -*- coding: utf-8 -*-
"""指令②: 只用高保真ETF重验证。行业指数生成信号(temp_v4<12 ∨ 自适应回撤), ETF执行。
处理: 上市前无价(引擎跳过)/停牌(陈旧价禁交易)/双边成本0.1%(佣金+冲击近似)。"""
import os
import sys
import time as _t

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd
import akshare as ak

import config
from carriers import CARRIERS, ALIAS
from engine import run_ledger, metrics, fmt

ETF_COST = 0.001  # 佣金+价差冲击近似/边


def fetch_etf(sym6, force=False):
    fp = os.path.join(config.DATA_DIR, f"etf_{sym6}.csv")
    if not force and os.path.exists(fp) and _t.time() - os.path.getmtime(fp) < 7 * 86400:
        return pd.read_csv(fp, parse_dates=["date"]).rename(columns={"收盘": "close"}).set_index("date")["close"].astype(float)
    code = sym6.split(".")[0]
    df = ak.fund_etf_hist_em(symbol=code, period="daily", start_date="20100101",
                             end_date="20261231", adjust="qfq")
    s = df.set_index(pd.to_datetime(df["日期"]))["收盘"].astype(float).sort_index()
    s.rename_axis("date").to_frame().to_csv(fp)
    return s


def temperature(c):
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)


obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]

# 行业指数 -> 高保真ETF 载体匹配
pairs = []
for sym, info in CARRIERS.items():
    if not info["enabled"]:
        continue
    cname = info["sector_name"]
    hit = None
    for n in dfs:
        if n == cname or cname in n or (ALIAS.get(n) == sym):
            hit = n
            break
    if hit:
        pairs.append((hit, sym, info["symbol"]))
    else:
        print(f"[WARN] {cname}({sym}) 未找到行业指数, 跳过")
print("载体配对:", [(a, c) for a, s, c in pairs])

idx_series, etf_series, names = {}, {}, []
for hit, sid, sym6 in pairs:
    d = dfs[hit]["close"].dropna()
    d.index = pd.to_datetime(d.index)
    d = d[d.index >= "2010-01-01"]
    if len(d) < 1250:
        print(f"[WARN] {hit} 指数历史不足, 跳过")
        continue
    try:
        e = fetch_etf(sym6)
    except Exception as ex:
        print(f"[FAIL] {sym6}: {type(ex).__name__} {str(ex)[:50]}")
        continue
    idx_series[sid] = d
    etf_series[sid] = e
    names.append(sid)

cal = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for s in etf_series.values()])))
PX = pd.DataFrame({n: etf_series[n].reindex(cal) for n in names})
SIG = pd.DataFrame(index=cal, columns=names, dtype=float)
EXT = pd.DataFrame(index=cal, columns=names, dtype=float)
STRE = pd.DataFrame(index=cal, columns=names, dtype=float)
for n in names:
    d = idx_series[n]
    t = temperature(d)
    dd = d / d.cummax() - 1.0
    dep = (-dd).rolling(1250, min_periods=250).max()
    thr = np.maximum(0.30, 0.70 * dep)
    SIG[n] = (((t < 12) | (dd <= -thr)) & (t > 0)).astype(float).reindex(cal).fillna(0)
    EXT[n] = ((t > 85) | (dd >= -0.25)).astype(float).reindex(cal).fillna(0)
    STRE[n] = (-dd.rolling(20).mean()).reindex(cal).fillna(-9.0)
catmap = {n: n for n in names}
res = run_ledger(PX, SIG, EXT, STRE, catmap, cal, max_pos=6, per_cat=2, cost=ETF_COST)
m = metrics(res["equity"].dropna())
eq = res["equity"].dropna()
print(f"\n[ETF-only 高保真载体验证] {fmt(m)} 终值{eq.iloc[-1]:.1f}")
print(f"成交笔数: {len(res['fills'])} | 载体: {len(names)}只 | 各载体上市日:")
for n in names:
    s = etf_series[n]
    print(f"  {n} {CARRIERS[[k for k, v in CARRIERS.items() if v['sector_name'] == ALIAS.get(CARRIERS[k]['sector_name'], k) or True][0]]['sector_name'] if False else n}: {s.index[0].date()} ~ {s.index[-1].date()} ({len(s)}日)")
yr = (1 + eq.pct_change().dropna()).groupby(eq.pct_change().dropna().index.year).prod() - 1
print("分年度:", {y: f"{v*100:+.0f}%" for y, v in yr.items()})

