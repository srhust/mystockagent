# -*- coding: utf-8 -*-
"""审查四十七轮: ①分阶段HOLD稳定性(视角1: 连续路径切片 2015-2018/2019-2021/2022-2026, 同一起点连续运行后切区间) ②到期-事件冲突归因(逐笔: 日期/股票/被跳过事件其后120日表现) ③90vs120收益集中度(交易级PnL, 删头部后是否仍优)。"""
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

STAGES = [("2015-2018", "2015-01-01", "2018-12-31"), ("2019-2021", "2019-01-01", "2021-12-31"), ("2022-2026", "2022-01-01", "2027-01-01")]

# ===== ①分阶段稳定性(连续路径切片) =====
print("=== ①分阶段稳定性: 连续路径切片(同一起点连续运行, 区间切片) ===")
for label in SNAPS:
    print(f"--- 评分器 {label} ---")
    print("阶段 | HOLD | V0收益/MDD/Sh/笔数 | V2aF收益/MDD/Sh/笔数")
    for sname, a, b in STAGES:
        row = [sname]
        for mode, rsv in [("V0", False), ("V2aF", False)]:
            cells = []
            for hold in (60, 90, 120, 180):
                collect = {}
                eq, _, fees = run_contract(TICKERS, SNAPS[label], mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect, reserve_mode=rsv, hold_days=hold)
                seg = eq.loc[a:b]
                r = seg.pct_change().dropna()
                ret = float(seg.iloc[-1] / seg.iloc[0] - 1)
                mdd = float((seg / seg.cummax() - 1).min())
                sh = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan
                nf = sum(1 for f2 in collect["fills"] if pd.Timestamp(a) <= f2[2] <= pd.Timestamp(b))
                cells.append(f"{ret:+.0%}/{mdd:.0%}/{sh:.2f}/{nf}")
            row.append(" | ".join(cells))
        print(f"{row[0]} | 60/90/120/180 → V0: {row[1]} || V2aF: {row[2]}")

# ===== ②冲突归因(12m/edge, V2aF, 90/120/180) =====
print("\n=== ②到期-事件冲突归因(12m/edge V2aF) ===")
PXc = {s: closes[s] for s in TICKERS}
for hold in (60, 90, 120, 180):
    collect = {}
    run_contract(TICKERS, SNAPS["12m/edge"], "V2aF", PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect, reserve_mode=False, hold_days=hold)
    cols = collect["collisions"]
    detail = []
    for s, d in cols:
        cc = PXc[s]
        if d in cc.index:
            i = cc.index.get_loc(d)
            if i + 120 < len(cc) and np.isfinite(cc.iloc[i + 120]):
                detail.append(f"{d.date()}/{s}/其后120日{cc.iloc[i+120]/cc.iloc[i]-1:+.0%}")
            else:
                detail.append(f"{d.date()}/{s}/末端")
    print(f"HOLD={hold}: {len(cols)}次冲突 → {'; '.join(detail[:12])}")

# ===== ③收益集中度: 90 vs 120 交易级PnL =====
print("\n=== ③90vs120 收益集中度(12m/edge, V0与V2aF) ===")
for mode, rsv in [("V0", False), ("V2aF", False)]:
    for hold in (90, 120):
        collect = {}
        run_contract(TICKERS, SNAPS["12m/edge"], mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect, reserve_mode=rsv, hold_days=hold)
        pnl = {}
        open_ent = {}
        for t in collect["fills"]:
            if t[3] == "ENTER":
                open_ent.setdefault(t[0], []).append((t[2], t[4], t[5]))
            elif t[3] == "EXIT" and open_ent.get(t[0]):
                d0, q0, p0 = open_ent[t[0]].pop(0)
                pnl[(t[0], d0)] = (t[5] - p0) * q0 - (t[6] + q0 * p0 * COST)
        v = sorted(pnl.values(), reverse=True)
        tot = sum(v)
        top5 = sum(v[:5])
        med = float(np.median(v)) if v else np.nan
        winr = float(np.mean([1.0 if x > 0 else 0.0 for x in v])) if v else np.nan
        print(f"{mode} HOLD={hold}: 笔数={len(v)} 总PnL={tot:.0f} 中位={med:+.0f} 胜率={winr:.0%} Top5贡献={top5:.0f}({top5/tot:.0%}) Top10贡献={sum(v[:10])/tot:.0%} 亏损笔合计={sum(x for x in v if x<0):.0f}")
