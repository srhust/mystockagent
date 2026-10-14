# -*- coding: utf-8 -*-
"""审查四十八轮: ①P0修正交易级PnL(HALVE/EXIT/V2AR_SELL卖出净收入全计入, ENTER按实际支出含费) + 总账对账恒等式 Σepisode_PnL == ΔNAV ②Top5静态集中度 + 反事实屏蔽重跑(block参数, 事后压力分析非策略) ③FCX冲突反事实(reentry_after_exit) ④全历史vs分段增量重放等价(前向影子模式核心性质)。"""
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


def episode_pnl(fills, last_px):
    """P0修正: PnL = Σ实际卖出净收入(HALVE/EXIT/V2AR_SELL, q*p−fee) − Σ实际买入总支出(ENTER, q*p+fee)。
    按ticker分别追踪episode(组合同时持有多标的)。返回 (episodes, 已实现合计, 未实现合计)"""
    eps = []
    cur = {}  # 每ticker一个当前episode
    for t in fills:
        s, sig, fd, act, q, p, fee = t
        if act == "ENTER":
            if cur.get(s):
                eps.append(cur[s])
            cur[s] = {"s": s, "sig": sig, "buy": q * p + fee, "sell": 0.0, "q_left": q, "open": True}
        elif act in ("HALVE", "V2AR_SELL", "EXIT"):
            e = cur.get(s)
            assert e is not None, f"卖出无未平episode: {t}"
            e["sell"] += q * p - fee
            e["q_left"] -= q
            if act == "EXIT":
                e["open"] = False
                eps.append(e)
                del cur[s]
    for s, e in cur.items():
        e["mkt"] = e["q_left"] * last_px.get(s, np.nan)
        eps.append(e)
    real = sum(e["sell"] - e["buy"] for e in eps if not e["open"])
    unreal = sum(e["sell"] - e["buy"] + e["mkt"] for e in eps if e["open"])
    return eps, real, unreal


print("=== ①P0修正后交易级PnL + 总账对账(12m/edge, V0/V2aF×90/120) ===")
LASTPX = {s: float(PX[s].iloc[-1]) for s in TICKERS}
CACHE = {}
for mode, rsv in [("V0", False), ("V2aF", False)]:
    for hold in (90, 120):
        collect = {}
        eq, _, fees = run_contract(TICKERS, SNAPS["12m/edge"], mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect, reserve_mode=rsv, hold_days=hold)
        eps, real, unreal = episode_pnl(collect["fills"], LASTPX)
        dnav = float(eq.iloc[-1]) - 100.0
        resid = dnav - (real + unreal) - collect["end_state"]["reserve"]["MU"] * 0  # V2aF非reserve模式reserve=0
        ok = abs(resid) < 0.01
        print(f"{mode} HOLD={hold}: 已实现={real:.0f} 未实现={unreal:.0f} ΔNAV={dnav:.0f} 残差={resid:.4f} {'PASS' if ok else 'FAIL'}")
        assert ok
        closed = [(e["s"], e["sig"], e["sell"] - e["buy"]) for e in eps if not e["open"]]
        v = sorted([x[2] for x in closed], reverse=True)
        top5 = sum(v[:5])
        print(f"  已了结{len(closed)}笔 总实现PnL={sum(v):.0f} 中位={np.median(v):+.0f} Top5={top5:.0f}({top5/sum(v):.0%}) Top10={sum(v[:10])/sum(v):.0%}")
        CACHE[(mode, hold)] = (collect, eps)

print("\n=== ②反事实屏蔽Top5(事后压力分析, 非可实施策略) ===")
for mode in ("V0", "V2aF"):
    for hold in (90, 120):
        collect, eps = CACHE[(mode, hold)]
        closed = sorted([(e["sell"] - e["buy"], e["s"], e["sig"]) for e in eps if not e["open"]], reverse=True)
        blk = {(s, sig) for _, s, sig in closed[:5]}
        collect2 = {}
        eq2, _, _ = run_contract(TICKERS, SNAPS["12m/edge"], mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=collect2, reserve_mode=False, hold_days=hold, block=blk)
        eps2, real2, unreal2 = episode_pnl(collect2["fills"], LASTPX)
        print(f"{mode} HOLD={hold}: 屏蔽Top5后 终值{float(eq2.iloc[-1]):.0f}(原{float(CACHE[(mode, hold)][0]['eq'].iloc[-1]):.0f}) 已实现{real2:.0f}(原{sum(x[0] for x in closed):.0f}) blocked计数={collect2['funnel'].get('blocked', 0)}")

print("\n=== ③FCX冲突反事实: 退出优先 vs 退出后同日重评(12m/edge V2aF, 冻结语义不动, 对照参数) ===")
for hold in (90, 120):
    c_off = {}
    eq_off, _, _ = run_contract(TICKERS, SNAPS["12m/edge"], "V2aF", PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=c_off, reserve_mode=False, hold_days=hold)
    c_on = {}
    eq_on, _, _ = run_contract(TICKERS, SNAPS["12m/edge"], "V2aF", PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=c_on, reserve_mode=False, hold_days=hold, reentry_after_exit=True)
    re_n = c_on["funnel"]["n4"] - c_off["funnel"]["n4"]
    print(f"HOLD={hold}: 退出优先终值={float(eq_off.iloc[-1]):.0f} 重评入场终值={float(eq_on.iloc[-1]):.0f} 新增入场={re_n}笔 冲突{c_off['funnel']['collide']}次")

print("\n=== ④全历史 vs 分段增量重放等价(前向影子核心性质, k=600切分) ===")
K = 600
for mode in ("V0", "V2aF"):
    for hold in (90, 120):
        c_full = {}
        eqF, shF, _ = run_contract(TICKERS, SNAPS["12m/edge"], mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=c_full, reserve_mode=False, hold_days=hold)
        c1 = {}
        eq1, _, _ = run_contract(TICKERS, SNAPS["12m/edge"], mode, PXd, MA100d, ev_all, LISTd, days_all[:K], OWNPOS, collect=c1, reserve_mode=False, hold_days=hold)
        st = c1["end_state"]
        c2 = {}
        eq2, _, _ = run_contract(TICKERS, SNAPS["12m/edge"], mode, PXd, MA100d, ev_all, LISTd, days_all[K:], OWNPOS, collect=c2, reserve_mode=False, hold_days=hold, init_state=st)
        eqJ = pd.concat([eq1.iloc[:-1], eq2])
        shJ = pd.concat([c1["sh"].iloc[:-1], c2["sh"]])
        d_eq = float((eqJ - eqF).abs().max())
        d_sh = float((shJ - c_full["sh"]).abs().max().max())
        d_cash = float((pd.concat([c1["cash"].iloc[:-1], c2["cash"]]) - c_full["cash"]).abs().max())
        nF = len([f for f in c_full["fills"] if f[2] >= days_all[K]])
        ok = d_eq < 1e-9 and d_sh < 1e-9 and d_cash < 1e-9 and nF == len(c2["fills"])
        print(f"{mode} HOLD={hold}: NAV最大差={d_eq:.2e} 股数最大差={d_sh:.2e} 现金最大差={d_cash:.2e} 后段成交数={nF}/{len(c2['fills'])} {'PASS(逐日一致)' if ok else 'FAIL'}")
        assert ok
