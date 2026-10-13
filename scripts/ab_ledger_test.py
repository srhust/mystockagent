# -*- coding: utf-8 -*-
"""A/B对照(T+1成交+费用+份额法账本, 主指标=账户净值CAGR):
A: 温度12/75 (6槽等权, 同类<=2)
B: V4 (temp12 或 自适应回撤, 修复-25%出场)
口径: 信号T日收盘生成 -> T+1收盘成交; 双边费0.15%; 共享资金池100。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

COST = 0.0015
CAP = 100.0
obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]


def temperature(c):
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)


cols, dmap, catmap = {}, {}, {}
for name, d in dfs.items():
    cs = d["close"].dropna()
    cs.index = pd.to_datetime(cs.index)
    cs = cs[cs.index >= "2010-01-01"]
    if len(cs) < 1250:
        continue
    cols[name] = cs
    dmap[name] = d
    catmap[name] = meta.get(name, {}).get("cat", "其他")
cal = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for s in cols.values()])))
PX = pd.DataFrame(cols).reindex(cal)
TEMPS = pd.DataFrame({n: temperature(s).reindex(cal) for n, s in cols.items()})
DD = pd.DataFrame({n: (s / s.cummax() - 1).reindex(cal) for n, s in cols.items()})
DEP = (-DD).rolling(1250, min_periods=250).max()
THR = np.maximum(0.30, 0.70 * DEP)
SIG_A = ((TEMPS < 12) & (TEMPS.shift(0) > 0)).astype(int)      # 温度锚
SIG_B = (((TEMPS < 12) | (DD <= -THR)) & (TEMPS > 0)).astype(int)  # V4锚
EXIT_A = (TEMPS > 75).astype(int)
EXIT_B = ((TEMPS > 85) | (DD >= -0.25)).astype(int)
STRE = pd.DataFrame({n: (1 / (1 + s.pct_change(60).rank(pct=True, axis=0))).reindex(cal) for n, s in cols.items()})
STRE = -DD.rolling(20).mean().reindex(cal)  # 强度: 短均回撤越深越优先


def run(sig, ext, max_pos=6, per_cat=2, greedy=False, risk=False, regime=False):
    hs = pd.read_csv(os.path.join(config.DATA_DIR, "idx_hs300.csv"), parse_dates=["date"]).set_index("date")["close"].astype(float)
    hs_ma = (hs / hs.rolling(200).mean() - 1.0).reindex(cal).fillna(0)  # >0 多头环境
    cash, eq = CAP, np.zeros(len(cal))
    pos = {}  # name -> shares
    entry = {}
    eqmax = CAP
    for i, d in enumerate(cal):
        mv = sum(sh * PX.at[d, n] for n, sh in pos.items() if np.isfinite(PX.at[d, n]))
        eq[i] = cash + mv
        eqmax = max(eqmax, eq[i])
        # --- T-1信号 -> 今日成交 ---
        sell = [n for n in list(pos) if ext.loc[cal[i - 1], n] == 1] if i > 0 else []
        for n in sell:
            px = PX.at[d, n]
            if np.isfinite(px):
                cash += pos[n] * px * (1 - COST)
                del pos[n], entry[n]
        slots = (10**9 if greedy else max_pos) - len(pos)
        buys = []
        breaker = risk and eq[i] < eqmax * 0.88  # 回撤12%熔断: 禁止新增风险
        if i > 0 and not breaker:
            cand = [n for n in cols if n not in pos and sig.loc[cal[i - 1], n] == 1 and np.isfinite(PX.at[d, n])]
            cand.sort(key=lambda n: STRE.at[d, n] if np.isfinite(STRE.at[d, n]) else 0)
            cats = {}
            for n in pos:
                cats[catmap[n]] = cats.get(catmap[n], 0) + 1
            for n in cand:
                if len(buys) >= slots:
                    break
                if not greedy and cats.get(catmap[n], 0) >= per_cat:
                    continue
                buys.append(n)
                cats[catmap[n]] = cats.get(catmap[n], 0) + 1
        if buys:
            w = eq[i] / max_pos if not greedy else eq[i] / (len(pos) + len(buys))
            if regime and hs_ma.loc[d] < 0:  # 市场环境过滤: 指数<MA200 时敞口减半
                w *= 0.5
            for n in buys:
                px = PX.at[d, n]
                spend = min(cash * 0.999, w)
                if risk:
                    spend = min(spend, eq[i] * 0.08)  # 单仓<=8%
                    if (eq[i] - cash) / eq[i] >= 0.70:  # 总暴露<=70%
                        break
                if spend > px * 0.01:
                    cash -= spend
                    pos[n] = pos.get(n, 0) + spend * (1 - COST) / px
                    entry[n] = d
    return pd.Series(eq, index=cal)


def metrics(eq):
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return f"CAGR {cagr * 100:+.2f}% 终值{eq.iloc[-1]:.2f} MDD {dd * 100:.1f}%"


for tag, sig, ext in [("A 温度12/75", SIG_A, EXIT_A), ("B V4三锚", SIG_B, EXIT_B)]:
    for mode, kw in [("槽位", dict(max_pos=6, per_cat=2)), ("贪心", dict(greedy=True))]:
        eq = run(sig, ext, **kw)
        print(f"{tag} [{mode}]: {metrics(eq)}")

print("\n[风控轨道: 单仓8% + 总暴露70% + 回撤12%熔断]")
for tag, sig, ext in [("A 温度12/75", SIG_A, EXIT_A), ("B V4三锚", SIG_B, EXIT_B)]:
    eq = run(sig, ext, max_pos=6, per_cat=2, risk=True)
    print(f"{tag} [槽位+风控]: {metrics(eq)}")

print("\n[Regime层: 沪深300<MA200 时敞口减半]")
for tag, sig, ext in [("A 温度12/75", SIG_A, EXIT_A), ("B V4三锚", SIG_B, EXIT_B)]:
    eq = run(sig, ext, max_pos=6, per_cat=2, regime=True)
    print(f"{tag} [槽位+regime]: {metrics(eq)}")

# ---- T+1 开盘价敏感性 ----
OPENX = pd.DataFrame({n: d["open"].dropna() for n, d in dmap.items()})
OPENX.index = pd.to_datetime(OPENX.index)
OPENX = OPENX.reindex(cal)
PX = OPENX  # run() 内全局引用 PX
print("\n[T+1 开盘价成交敏感性]")
for tag, sig, ext in [("A 温度12/75", SIG_A, EXIT_A), ("B V4三锚", SIG_B, EXIT_B)]:
    eq = run(sig, ext, max_pos=6, per_cat=2)
    print(f"{tag} [槽位/开盘]: {metrics(eq)}")
