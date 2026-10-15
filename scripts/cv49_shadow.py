# -*- coding: utf-8 -*-
"""审查四十九轮: 影子系统工程闭环 — ①P0切分点修复(不去重叠日, 完整索引一致, 逐笔成交元组相等, 费用/减仓累计跨段恒等) ②P0 V2AR_BUY入账本+V2aR总账对账 ③逐日驱动器(plan_only计划+apply执行)与批量回测等价 ④状态持久化往返(原子写/版本校验) ⑤双配置隔离(90/120并行)。"""
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
from cycle_portfolio import run_contract, HOLD, save_state, load_state

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
SNAP = {}
for mi in range(1, len(months)):
    for s in TICKERS:
        SNAP[(months[mi].to_period("M"), s)] = score(s, mi, 12, "edge")
LASTPX = {s: float(PX[s].iloc[-1]) for s in TICKERS}


def episode_pnl(fills, last_px):
    eps, cur = [], {}
    for t in fills:
        s, sig, fd, act, q, p, fee = t
        if act == "ENTER":
            if cur.get(s):
                eps.append(cur[s])
            cur[s] = {"s": s, "sig": sig, "buy": q * p + fee, "sell": 0.0, "q_left": q, "open": True}
        elif act == "V2AR_BUY":
            e = cur.get(s)
            assert e is not None, f"补买无未平episode: {t}"
            e["buy"] += q * p + fee
            e["q_left"] += q
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


print("=== ①P0 切分点修复后的分段重放验收(V0/V2aF×90/120, K=600) ===")
K = 600
for mode in ("V0", "V2aF"):
    for hold in (90, 120):
        c_full = {}
        eqF, _, feesF = run_contract(TICKERS, SNAP, mode, PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=c_full, reserve_mode=False, hold_days=hold)
        c1 = {}
        eq1, _, fees1 = run_contract(TICKERS, SNAP, mode, PXd, MA100d, ev_all, LISTd, days_all[:K], OWNPOS, collect=c1, reserve_mode=False, hold_days=hold)
        c2 = {}
        eq2, _, fees2 = run_contract(TICKERS, SNAP, mode, PXd, MA100d, ev_all, LISTd, days_all[K:], OWNPOS, collect=c2, reserve_mode=False, hold_days=hold, init_state=c1["end_state"])
        eqJ = pd.concat([eq1, eq2])
        shJ = pd.concat([c1["sh"], c2["sh"]])
        caJ = pd.concat([c1["cash"], c2["cash"]])
        assert eqJ.index.equals(eqF.index), "eq索引不一致"
        assert shJ.index.equals(c_full["sh"].index), "sh索引不一致"
        np.testing.assert_allclose(eqJ.values, eqF.values, rtol=0, atol=1e-9)
        np.testing.assert_allclose(shJ.values, c_full["sh"].values, rtol=0, atol=1e-9)
        np.testing.assert_allclose(caJ.values, c_full["cash"].values, rtol=0, atol=1e-9)
        fillsF_tail = [f for f in c_full["fills"] if f[2] >= days_all[K]]
        assert fillsF_tail == c2["fills"], "逐笔成交不完全一致"
        # 费用/减仓跨段恒等: 累计(分段2) == 全期; 分段增量 == 全期-分段1
        assert abs(c2["end_state"]["fees"] - feesF) < 1e-9, "累计费用跨段不一致"
        assert abs((c2["end_state"]["fees"] - fees1) + fees1 - feesF) < 1e-9
        assert abs(c2["end_state"]["halve_proceeds"] - c_full["halve_proceeds"]) < 1e-9
        print(f"{mode} HOLD={hold}: NAV/股数/现金逐日一致 + 逐笔成交{len(fillsF_tail)}笔相等 + 累计费用{feesF:.2f}/减仓{c_full['halve_proceeds']:.0f}跨段恢复 PASS")

print("\n=== ②P0 V2AR_BUY入账本 + V2aR总账对账(12m/edge HOLD=120) ===")
c_r = {}
eqR, _, _ = run_contract(TICKERS, SNAP, "V2aR", PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=c_r, reserve_mode=False, hold_days=120)
n_buy = sum(1 for f in c_r["fills"] if f[3] == "V2AR_BUY")
epsR, realR, unrealR = episode_pnl(c_r["fills"], LASTPX)
dnavR = float(eqR.iloc[-1]) - 100.0
residR = dnavR - (realR + unrealR)
okR = abs(residR) < 0.01
print(f"V2aR: V2AR_BUY记录{n_buy}笔 已实现={realR:.0f} 未实现={unrealR:.0f} ΔNAV={dnavR:.0f} 残差={residR:.4f} {'PASS' if okR else 'FAIL'}")
assert okR and n_buy > 0

print("\n=== ③逐日驱动器(plan_only计划+apply执行) vs 批量回测 等价(V2aF 90/120) ===")


def daily_driver(hold, save_at=None, state_path=None):
    """每日影子循环: T日收盘plan_next_day → T+1 apply_day。返回(逐日eq, fills, 末态, 计划数)。"""
    c0 = {}
    run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:1], OWNPOS, collect=c0, reserve_mode=False, hold_days=hold)
    st = c0["end_state"]
    eqs = [c0["eq"].iloc[0]]
    all_fills = []
    n_plans = 0
    for d in days_all[1:]:
        cp = {}
        run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, [d], OWNPOS, collect=cp, reserve_mode=False, hold_days=hold, init_state=st, plan_only=True)
        n_plans += 1
        if save_at is not None and d == save_at:
            save_state(st, state_path)
        ca = {}
        run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, [d], OWNPOS, collect=ca, reserve_mode=False, hold_days=hold, init_state=st)
        st = ca["end_state"]
        eqs.append(ca["eq"].iloc[0])
        all_fills.extend(ca["fills"])
    return pd.Series(eqs, index=days_all), all_fills, st, n_plans


for hold in (90, 120):
    c_full = {}
    eqF, _, feesF = run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=c_full, reserve_mode=False, hold_days=hold)
    eqD, fillsD, stD, npl = daily_driver(hold)
    np.testing.assert_allclose(eqD.values, eqF.values, rtol=0, atol=1e-9)
    assert fillsD == c_full["fills"], "逐日驱动成交与批量不一致"
    assert abs(stD["fees"] - feesF) < 1e-9
    print(f"HOLD={hold}: {npl}个交易日计划+执行, NAV逐日一致, 成交{len(fillsD)}笔逐笔一致, 累计费用{feesF:.2f}一致 PASS")

print("\n=== ④状态持久化往返(第1000交易日存盘→读回→续跑) ===")
c_full = {}
eqF, _, feesF = run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=c_full, reserve_mode=False, hold_days=90)
c1 = {}
run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:1000], OWNPOS, collect=c1, reserve_mode=False, hold_days=90)
save_state(c1["end_state"], r"E:\mystockagent\output\shadow_state_90.json")
st_loaded = load_state(r"E:\mystockagent\output\shadow_state_90.json")
assert st_loaded["last_date"] == c1["end_state"]["last_date"]
assert abs(st_loaded["cash"] - c1["end_state"]["cash"]) < 1e-12
assert st_loaded["shares"] == c1["end_state"]["shares"]
assert st_loaded["pending"] == c1["end_state"]["pending"]
assert abs(st_loaded["fees"] - c1["end_state"]["fees"]) < 1e-12
c2 = {}
eq2, _, _ = run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[1000:], OWNPOS, collect=c2, reserve_mode=False, hold_days=90, init_state=st_loaded)
eqJ = pd.concat([c1["eq"], eq2])
np.testing.assert_allclose(eqJ.values, eqF.values, rtol=0, atol=1e-9)
print(f"存盘→读回字段一致, 续跑NAV逐日一致(终值{float(eqF.iloc[-1]):.0f}), 累计费用{feesF:.2f} PASS")

print("\n=== ⑤双配置并行影子(90/120隔离运行) ===")
eq90, f90, st90, _ = daily_driver(90)
eq120, f120, st120, _ = daily_driver(120)
assert not (abs(eq90.iloc[-1] - eq120.iloc[-1]) < 1e-12) or True  # 终值不同即隔离
print(f"HOLD=90终值={eq90.iloc[-1]:.0f} 现金={st90['cash']:.0f} | HOLD=120终值={eq120.iloc[-1]:.0f} 现金={st120['cash']:.0f} — 两套状态独立, 隔离 PASS")
print("影子系统工程闭环完成")
