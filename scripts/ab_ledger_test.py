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
# 消融C: V4 + 商品过滤(锚②v2: 位置<0.6 放行; >=0.6 需20日动量>0或创60日新高)
SEC2FUT = {n: m.get("fut") for n, m in meta.items() if m.get("fut")}
COMMO_OK = pd.DataFrame(True, index=cal, columns=list(cols))
for n, fut in SEC2FUT.items():
    if n not in cols or fut is None:
        continue
    fp = os.path.join(config.DATA_DIR, f"fut_{fut}.csv")
    if not os.path.exists(fp):
        continue
    f = pd.read_csv(fp, parse_dates=["date"]).set_index("date")["close"].astype(float)
    pos_ = ((f - f.rolling(1250, min_periods=250).min()) /
            (f.rolling(1250, min_periods=250).max() - f.rolling(1250, min_periods=250).min())).reindex(cal)
    mom = (f / f.shift(20) - 1).reindex(cal)
    brk = (f / f.rolling(60).max() - 1).reindex(cal) >= 0
    COMMO_OK[n] = (pos_ < 0.6) | (mom > 0) | (brk.fillna(False))
SIG_C = (SIG_B == 1) & COMMO_OK.reindex(cal).fillna(True)
SIG_C = SIG_C.astype(int)
STRE = pd.DataFrame({n: (1 / (1 + s.pct_change(60).rank(pct=True, axis=0))).reindex(cal) for n, s in cols.items()})
STRE = -DD.rolling(20).mean().reindex(cal)  # 强度: 短均回撤越深越优先


def run(sig, ext, max_pos=6, per_cat=2, greedy=False, risk=False, regime=False, tranches=False, top_exit=False):
    hs = pd.read_csv(os.path.join(config.DATA_DIR, "idx_hs300.csv"), parse_dates=["date"]).set_index("date")["close"].astype(float)
    hs_ma = (hs / hs.rolling(200).mean() - 1.0).reindex(cal).fillna(0)  # >0 多头环境
    cash, eq = CAP, np.zeros(len(cal))
    pos = {}   # name -> shares
    last_px = {}  # name -> 最近有效价(仅估值用, 陈旧>20日计入stale且禁止交易)
    stale = {}
    entry = {}
    pos_state = {}  # name -> [entry_px, stage] (tranches用)
    eqmax = CAP
    for i, d in enumerate(cal):
        # 估值: 缺价用最近有效价(受控前向填充), 并记录陈旧天数
        mv, stale_n = 0.0, 0
        for n, sh in pos.items():
            px = PX.at[d, n] if n in PX.columns else np.nan
            if np.isfinite(px):
                last_px[n], stale[n] = float(px), 0
            else:
                px = last_px.get(n)
                stale[n] = stale.get(n, 0) + 1
                stale_n += stale[n] > 20
            if px is not None and np.isfinite(px):
                mv += sh * px
        eq[i] = cash + mv
        eqmax = max(eqmax, eq[i])
        sig_day = cal[i - 1] if i > 0 else None  # 所有决策输入只用T-1收盘前信息
        # --- 卖出: T-1出场信号, T日可成交(需当日有价) ---
        sell = [n for n in list(pos) if sig_day is not None and ext.loc[sig_day, n] == 1] if i > 0 else []
        for n in sell:
            px = PX.at[d, n] if n in PX.columns else np.nan
            if np.isfinite(px):
                cash += pos[n] * px * (1 - COST)
                del pos[n], entry[n], last_px[n], stale[n]
                pos_state.pop(n, None)
        slots = (10**9 if greedy else max_pos) - len(pos)
        buys = []
        breaker = risk and eq[i] < eqmax * 0.88
        regime_weak = regime and sig_day is not None and hs_ma.loc[sig_day] < 0
        if regime_weak:  # regime走弱: 已有仓减25%(调校后, 原50%过度削减深寒建仓)
            for n in list(pos):
                px = PX.at[d, n] if n in PX.columns else np.nan
                if np.isfinite(px) and stale.get(n, 0) <= 20:
                    cut = pos[n] * 0.25
                    cash += cut * px * (1 - COST)
                    pos[n] -= cut
        # D: 三档摊平加仓(30/35/35): 持仓跌破入场价-15%/-30%时各加一份
        if tranches and i > 0:
            for n in list(pos):
                st = pos_state.get(n)
                if st is None or st[1] >= 2:
                    continue
                px = PX.at[d, n] if n in PX.columns else np.nan
                if not np.isfinite(px) or stale.get(n, 0) > 20:
                    continue
                tier = -0.15 if st[1] == 0 else -0.30
                if px <= st[0] * (1 + tier):
                    spend = min(cash * 0.999, eq[i] / max_pos * 0.35)
                    if spend > px * 0.001:
                        cash -= spend
                        pos[n] += spend * (1 - COST) / px
                        st[1] += 1
        if i > 0 and not breaker:
            cand = [n for n in cols if n not in pos and sig.loc[sig_day, n] == 1
                    and np.isfinite(PX.at[d, n])]
            cand.sort(key=lambda n: STRE.at[sig_day, n] if np.isfinite(STRE.at[sig_day, n]) else float("-inf"))
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
            if regime_weak:
                w *= 0.5
            for n in buys:
                px = PX.at[d, n]
                spend = min(cash * 0.999, w * (0.30 if tranches else 1.0))
                if risk:
                    spend = min(spend, eq[i] * 0.08)
                    headroom = max(0.0, 0.70 * eq[i] - (eq[i] - cash))  # 可增敞口
                    spend = min(spend, headroom)
                    if spend <= px * 0.01:
                        continue
                if spend > px * 0.001:
                    cash -= spend
                    pos[n] = pos.get(n, 0) + spend * (1 - COST) / px
                    entry[n] = d
                    if tranches:
                        pos_state[n] = [float(px), 0]
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

EXIT_C = EXIT_B
EXIT_E = (TEMPS > 85).astype(int)  # E: 仅顶部退出(无回撤修复出场), 分离顶部退出贡献
print("\n[消融实验 A/B/C/D/E (净值口径, 修正后账本)]")
for tag, sig, ext, kw in [("A", SIG_A, EXIT_A, {}), ("B", SIG_B, EXIT_B, {}),
                          ("C", SIG_C, EXIT_C, {}), ("D", SIG_C, EXIT_C, dict(tranches=True)),
                          ("E", SIG_C, EXIT_E, dict(tranches=True))]:
    eq = run(sig, ext, max_pos=6, per_cat=2, **kw)
    print(f"{tag} [槽位]: {metrics(eq)}")

# ---- T+1 开盘价敏感性 ----
OPENX = pd.DataFrame({n: d["open"].dropna() for n, d in dmap.items()})
OPENX.index = pd.to_datetime(OPENX.index)
OPENX = OPENX.reindex(cal)
PX = OPENX  # run() 内全局引用 PX
print("\n[T+1 开盘价成交敏感性]")
for tag, sig, ext in [("A 温度12/75", SIG_A, EXIT_A), ("B V4三锚", SIG_B, EXIT_B)]:
    eq = run(sig, ext, max_pos=6, per_cat=2)
    print(f"{tag} [槽位/开盘]: {metrics(eq)}")

