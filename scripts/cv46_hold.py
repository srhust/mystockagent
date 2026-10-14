# -*- coding: utf-8 -*-
"""审查四十六轮: 纯持有期消融(A设计) — 评分器保持120日冻结版本, 组合HOLD=60/90/120/180, V0与V2aF-Recycle×三评分器。
输出: 终值/CAGR/Sharpe/MDD/Calmar/入场数/平均持仓天数/现金占用率/HALVE次数与释放/费用/到期-事件同日冲突数/分年收益(2011/2020/2025)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features
import mu_af_v2 as M
from cycle_portfolio import run_contract, HOLD

D = os.path.join(config.DATA_DIR, "mu")
COST, DEDUP = 0.0015, 60
TICKERS = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]
closes, dp_ev, v4_ev, ma100s = {}, {}, {}, {}
for sym in TICKERS:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    dp_ev[sym] = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    ma100s[sym] = c.rolling(100).mean()
    closes[sym] = c
v4tr = (M.v4.reindex(closes["MU"].index).fillna(0) > 0) & (M.v4.reindex(closes["MU"].index).fillna(0).diff() > 0)
v4_ev["MU"] = v4tr


def events(sym):
    rw = dp_ev[sym].values.copy()
    if sym in v4_ev:
        rw = rw | v4_ev[sym].values
    out, last = set(), -9999
    for i, e in enumerate(rw):
        if e and i - last >= DEDUP:
            out.add(i)
            last = i
    return out


ev_all = {s: events(s) for s in TICKERS}
OWNPOS = {s: pd.Series(np.arange(len(closes[s])), index=closes[s].index) for s in TICKERS}
idx_all = pd.concat(closes.values()).index.unique().sort_values()
PX = pd.DataFrame({s: closes[s].reindex(idx_all) for s in TICKERS}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx_all).notna() for s in TICKERS})
PXd = {s: pd.Series(PX[s].values, index=idx_all) for s in TICKERS}
MA100d = {s: pd.Series(ma100s[s].reindex(idx_all).values, index=idx_all) for s in TICKERS}
LISTd = {s: LISTED[s] for s in TICKERS}
POSO = {}
for s in TICKERS:
    ev2 = ev_all[s]
    cc = closes[s]
    nn = len(cc)
    p = np.zeros(nn)
    ip = False
    e_i = -999
    for i in range(nn):
        if not ip and i in ev2:
            ip, e_i = True, i
        elif ip and i - e_i >= HOLD:
            ip = False
        p[i] = 1.0 if ip else 0.0
    POSO[s] = pd.Series(p, index=cc.index)


def ledger_nav(cc, p):
    cash, shares = 100.0, 0.0
    nav = pd.Series(np.nan, index=cc.index)
    pv = p.reindex(cc.index).fillna(0.0).values
    pxs2 = cc.values
    ip = False
    for i in range(1, len(cc)):
        want = bool(pv[i - 1] >= 0.5)
        if want and not ip:
            shares, cash, ip = cash * (1 - COST) / pxs2[i], 0.0, True
        elif (not want) and ip:
            cash, shares, ip = shares * pxs2[i] * (1 - COST), 0.0, False
        nav.iloc[i] = cash + shares * pxs2[i]
    return nav.ffill().fillna(100.0)


NAV = {s: ledger_nav(closes[s], POSO[s].reindex(closes[s].index)) for s in TICKERS}
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index
SMR = pd.DataFrame({s: NAV[s].reindex(idx_all).resample("ME").last().pct_change() for s in TICKERS}).reindex(months).fillna(0.0)


def score(sym, mi, win, kind):
    if kind == "price":
        w = MCLOSE[sym].iloc[max(0, mi - win):mi].dropna()
        if len(w) < win or w.iloc[0] <= 0:
            return np.nan
        eqw = w / w.iloc[0]
    else:
        w = SMR[sym].iloc[max(0, mi - win):mi]
        if len(w) < win:
            return np.nan
        eqw = (1 + w).cumprod()
    mdd = float((eqw / eqw.cummax() - 1).min())
    return float(eqw.iloc[-1]) / abs(mdd) if mdd else np.nan


MI0 = 72
sd0 = idx_all[idx_all.to_period("M") == months[MI0].to_period("M")][0]
days_all = idx_all[idx_all.get_loc(sd0):]
SNAPS = {}
for label, (win, kind) in {"36m/price": (36, "price"), "12m/edge": (12, "edge"), "36m/edge": (36, "edge")}.items():
    snap = {}
    for mi in range(1, len(months)):
        for s in TICKERS:
            snap[(months[mi].to_period("M"), s)] = score(s, mi, win, kind)
    SNAPS[label] = snap

print("=== 纯持有期消融(A设计: 评分冻结120日版, 仅组合HOLD改变) ===")
print("HOLD | 模式 | 评分器 | 终值 CAGR Sharpe MDD Calmar | 入场/均持仓天/现金占用% | HALVE次数/释放 | 费用 | 冲突 | 2011/2020/2025")
for hold in (60, 90, 120, 180):
    for mode, rsv in [("V0", False), ("V2aF", False)]:
        for label in SNAPS:
            collect = {}
            eq, sh, fees = run_contract(TICKERS, SNAPS[label], mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect, reserve_mode=rsv, hold_days=hold)
            f = collect["funnel"]
            r = eq.pct_change().dropna()
            years = (eq.index[-1] - eq.index[0]).days / 365.25
            cagr = (float(eq.iloc[-1]) / 100.0) ** (1 / years) - 1
            mdd = float((eq / eq.cummax() - 1).min())
            sharpe = float(r.mean() / r.std() * np.sqrt(252))
            calmar = cagr / abs(mdd) if mdd else np.nan
            # 平均持仓天数(ENTER→EXIT配对)
            holds = []
            open_ent = {}
            for t in collect["fills"]:
                if t[3] == "ENTER":
                    open_ent.setdefault(t[0], []).append(t[2])
                elif t[3] == "EXIT" and t[0] in open_ent and open_ent[t[0]]:
                    d0 = open_ent[t[0]].pop(0)
                    holds.append((t[2] - d0).days)
            avg_hold = np.mean(holds) if holds else np.nan
            idle = float((collect["cash"] / eq).mean())
            yr = eq.resample("YE").last().pct_change()
            y = {str(d.year): float(v) for d, v in yr.items()}
            print(f"{hold} | {mode} | {label} | {float(eq.iloc[-1]):.0f} {cagr:+.1%} {sharpe:.2f} {mdd:.0%} {calmar:.2f} | {f['n4']}/{avg_hold if np.isfinite(avg_hold) else -1:.0f}/{idle:.0%} | {sum(1 for x in collect['fills'] if x[3]=='HALVE')}/{collect['halve_proceeds']:.0f} | {fees:.0f} | {f['collide']} | {y.get('2011', float('nan')):+.0%}/{y.get('2020', float('nan')):+.0%}/{y.get('2025', float('nan')):+.0%}")
