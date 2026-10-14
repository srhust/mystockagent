# -*- coding: utf-8 -*-
"""审查三十五轮: ①P0 V2a语义对照(Freeze一次减半后冻结股数 vs Rebalance每日50%NAV) + NAV恒等式检验
②P1 同暴露对照(V0缩至47%恒定) ③P1 池级验证(三评分器×{无覆盖/V2a-Freeze/V2a-Rebalance}, m∈{1,0.5}由前日MA100状态)
④P1 四案例逐笔尾部归因(破位日/减仓成交日/减仓前后MAE/最终收益/错失反弹)。"""
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

D = os.path.join(config.DATA_DIR, "mu")
COST = 0.0015
HOLD, DEDUP = 120, 60
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


def run_single(sym, mode):
    """mode: V0 / V2aF(freeze) / V2aR(rebalance) / V0x47(恒定0.47)。返回(权益, 交易, 日志, 买次, 卖次)。"""
    c = closes[sym]
    pxs = c.values
    m100 = ma100s[sym].values
    ev = events(sym)
    n = len(c)
    cash, shares = 100.0, 0.0
    in_pos = False
    entry_i = -999
    frozen = False
    eq = pd.Series(np.nan, index=c.index)
    eq.iloc[0] = 100.0
    trades, log = [], []
    nb = ns = 0
    fee_tot = 0.0
    prev_sh = 0.0
    prev_p = pxs[0]
    for i in range(1, n):
        p = pxs[i]
        sd = i - 1
        fee_day = 0.0
        if in_pos and frozen:
            tgt_level = None  # 冻结: 不再调仓, 只在退出时清仓
        else:
            if not in_pos and sd in ev:
                tgt_level = 0.47 if mode == "V0x47" else 1.0
                new_entry = True
            elif in_pos and mode == "V0x47":
                tgt_level = 0.47 if sd - entry_i < HOLD else 0.0
            elif in_pos and sd - entry_i >= HOLD:
                tgt_level = 0.0
            elif in_pos and mode in ("V2aF", "V2aR") and pxs[sd] < m100[sd]:
                tgt_level = 0.5
                if not log or log[-1][1] != "减半" or (c.index[i].date() - log[-1][0]).days > 30:
                    log.append((c.index[i].date(), "减半", f"{pxs[sd]:.2f}<MA100{m100[sd]:.2f}"))
            else:
                tgt_level = 1.0 if in_pos else 0.0
        nav = cash + shares * p
        fee_day = 0.0
        if in_pos and frozen:
            if sd - entry_i >= HOLD:
                tgt_level = 0.0
        if in_pos and tgt_level == 0.0:
            cash += shares * p * (1 - COST)
            fee_day += shares * p * COST
            ns += 1
            fee_tot += shares * p * COST
            trades[-1][1] = i
            shares, in_pos, frozen = 0.0, False, False
        elif not in_pos and tgt_level is not None and tgt_level > 0:
            spend = min(nav * tgt_level, cash)
            shares += spend * (1 - COST) / p
            fee_day += spend * COST
            cash -= spend
            nb += 1
            fee_tot += spend * COST
            in_pos, entry_i, frozen = True, sd, False
            trades.append([i, None, p, None])
        elif in_pos and tgt_level is not None and abs(tgt_level - shares * p / (cash + shares * p)) > 1e-6:
            cur = shares * p
            tgt = tgt_level * (cash + shares * p)
            if tgt < cur:
                sell_sh = (cur - tgt) / p
                cash += sell_sh * p * (1 - COST)
                fee_day += sell_sh * p * COST
                shares -= sell_sh
                ns += 1
                fee_tot += sell_sh * p * COST
                if mode == "V2aF":
                    frozen = True  # 一次减半后冻结股数
            elif tgt > cur and cash > 1e-9:
                buy = min(tgt - cur, cash)
                shares += buy * (1 - COST) / p
                fee_day += buy * COST
                cash -= buy
                nb += 1
                fee_tot += buy * COST
        eq.iloc[i] = cash + shares * p
        # NAV恒等式: 当日变化 = 隔夜份额×价差 - 当日费用
        expect = eq.iloc[i - 1] + prev_sh * (p - prev_p) - fee_day
        assert abs(eq.iloc[i] - expect) < 0.5, f"NAV恒等式失败 {c.index[i].date()}"
        prev_sh, prev_p = shares, p
    return eq.ffill().fillna(100.0), trades, log, nb, ns, fee_tot


print("=== P0 V2a语义对照(MU) + NAV恒等式每日断言通过 ===")
for mode in ("V0", "V0x47", "V2aF", "V2aR"):
    eq, trades, log, nb, ns, fees = run_single("MU", mode)
    r = eq.pct_change().dropna()
    mdd = float((eq / eq.cummax() - 1).min())
    expo = 1 - (1 - 0) if mode != "x" else 0
    print(f"{mode}: 终值{float(eq.iloc[-1]):.0f} MDD{mdd:.0%} Sharpe{float(r.mean()/r.std()*np.sqrt(252)):.2f} Calmar{float(eq.iloc[-1])** (1/17.8)/abs(mdd):.2f} 买{nb} 卖{ns} 费{fees:.0f} 减半日志{len(log)}")

print("\n=== P1 四案例逐笔归因(V2aF) ===")
eqF, tradesF, logF, _, _, _ = run_single("MU", "V2aF")
c = closes["MU"]
pxs = c.values
m100 = ma100s["MU"].values
n = len(c)
for tr in tradesF:
    ef, xf = tr[0], tr[1]
    if xf is None:
        xf = n - 1
    yr = c.index[ef].year
    if yr in (2011, 2016, 2020, 2025) or (2026 == yr):
        # 破位日: 入场后首个收盘<MA100
        brk = next((k for k in range(ef, min(n, ef + HOLD)) if pxs[k] < m100[k]), None)
        halve_fill = min(brk + 1, n - 1) if brk is not None else None
        mae_before = float(min(pxs[ef:min(n, (brk + 1) if brk else xf) + 1]) / tr[2] - 1) if brk else float(min(pxs[ef:xf + 1]) / tr[2] - 1)
        mae_after = float(min(pxs[halve_fill:xf + 1]) / pxs[halve_fill] - 1) if halve_fill and halve_fill < xf else 0.0
        missed = float(pxs[xf] / pxs[halve_fill] - 1) if halve_fill and halve_fill < xf else 0.0
        ret = float(pxs[xf] / tr[2] * (1 - COST) ** 2 - 1)
        print(f"{c.index[ef].date()}入场@{tr[2]:.2f}: 破位{c.index[brk].date() if brk else '未'} 减仓成交{c.index[halve_fill].date() if halve_fill else '-'} 减仓前MAE{mae_before:.0%} 减仓后段MAE{mae_after:.0%} 错失反弹{missed:.0%} 整笔净{ret:+.0%}")

print("\n=== P1 池级验证(三评分×{无/V2aF/V2aR}) ===")
POSO = pd.DataFrame({s: pd.Series(0.0, index=closes[s].index) for s in TICKERS}).fillna(0.0)
for s in TICKERS:
    ev = events(s)
    cc = closes[s]
    nn = len(cc)
    p = np.zeros(nn)
    ip = False
    e_i = -999
    for i in range(nn):
        if not ip and i in ev:
            ip, e_i = True, i
        elif ip and i - e_i >= HOLD:
            ip = False
        p[i] = 1.0 if ip else 0.0
    POSO[s] = pd.Series(p, index=cc.index)
idx = pd.concat(closes.values()).index.unique().sort_values()
POSO = POSO.reindex(idx).fillna(0.0)
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in TICKERS}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx).notna() for s in TICKERS})
MRISK = pd.DataFrame({s: (closes[s] < ma100s[s]).astype(float).reindex(idx) for s in TICKERS}).fillna(0.0)
MFRZ = pd.DataFrame(0.0, index=idx, columns=TICKERS)
for s in TICKERS:
    seen = False
    for d in idx:
        if POSO[s].get(d, 0.0) < 0.5:
            seen = False
            continue
        if MRISK[s].get(d, 0.0) > 0:
            seen = True
        MFRZ[s][d] = 0.5 if seen else 1.0
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index
NAVx = {s: run_single(s, "V0")[0] for s in ["MU"]}  # 仅MU真实账本复用, 其余用V0近似(评分用)
SMR = pd.DataFrame({s: (POSO[s].shift(1).fillna(0) * PX[s].pct_change().fillna(0)) for s in TICKERS}).resample("ME").apply(lambda x: (1 + x).prod() - 1)


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


def build_schedule(mi0, mi1, win, kind):
    sched = {}
    for mi in range(mi0, mi1):
        m = months[mi]
        t_day = idx[idx.to_period("M") == m.to_period("M")][0]
        scores, act = {}, {}
        for s in TICKERS:
            sc = score(s, mi, win, kind)
            if np.isfinite(sc):
                scores[s] = sc
                act[s] = bool(POSO[s].reindex(idx[:idx.get_loc(t_day) + 1]).iloc[-1] >= 0.5)
        elig = {s: v for s, v in scores.items() if act.get(s)}
        w = {}
        if elig:
            rank = sorted(elig, key=elig.get, reverse=True)
            w = {rank[0]: 0.6} | ({rank[1]: 0.4} if len(rank) > 1 else {})
        sched[idx[idx.get_loc(t_day) + 1]] = w
    return sched


def run_pool(sched, risk_mode, day_slice, budget=100.0):
    cash = float(budget)
    shares = {s: 0.0 for s in TICKERS}
    cur_w = {s: 0.0 for s in TICKERS}
    exd = set(sched)
    eq = pd.Series(np.nan, index=day_slice)
    idle_sum = idle_n = 0
    s_i = idx.get_loc(day_slice[0])
    for k, d in enumerate(day_slice):
        i = s_i + k
        sd = idx[i - 1] if i > 0 else d
        if d in exd:
            cur_w = dict(sched[d])
        px = PX.loc[d]
        for s in TICKERS:
            m = (MFRZ[s].get(sd, 1.0) if risk_mode == "V2aF" else MRISK[s].get(sd, 0.0)) if risk_mode else 0.0
            mult = 0.5 if (risk_mode and m > 0) else 1.0  # F: m为冻结系数0.5/1.0; R: m为当日破位
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * POSO[s].get(sd, 0.0) * mult * (cash + sum(shares[u] * px[u] for u in TICKERS if shares[u] > 0 and np.isfinite(px[u])))
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt < cur_v:
                dv = tgt - cur_v
                cash -= dv + abs(dv) * COST
                shares[s] += dv / px[s]
        pv = cash + sum(shares[s] * px[s] for s in TICKERS if shares[s] > 0 and np.isfinite(px[s]))
        buys = {}
        for s in TICKERS:
            m = (MFRZ[s].get(sd, 1.0) if risk_mode == "V2aF" else MRISK[s].get(sd, 0.0)) if risk_mode else 0.0
            mult = 0.5 if (risk_mode and m > 0) else 1.0  # F: m为冻结系数0.5/1.0; R: m为当日破位
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * POSO[s].get(sd, 0.0) * mult * pv
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt > cur_v:
                buys[s] = tgt - cur_v
        need = sum(buys.values())
        sc = min(1.0, max(cash, 0.0) / (1 + COST) / need) if need > 0 else 0.0
        for s, dv in buys.items():
            dv *= sc
            if abs(dv) > pv * 1e-6:
                cash -= dv + dv * COST
                shares[s] += dv / px[s]
        pv = cash + sum(shares[s] * px[s] for s in TICKERS if shares[s] > 0 and np.isfinite(px[s]))
        assert cash >= -1e-8 and pv > 0
        eq.loc[d] = pv
        idle_sum += cash / pv
        idle_n += 1
    return eq, idle_sum / max(idle_n, 1)


MI0 = 72
sd0 = idx[idx.to_period("M") == months[MI0].to_period("M")][0]
days_all = idx[idx.get_loc(sd0):]
for label, (win, kind) in {"36m/price": (36, "price"), "12m/edge": (12, "edge"), "36m/edge": (36, "edge")}.items():
    sched = build_schedule(MI0, len(months), win, kind)
    out = [label]
    for rk in (None, "V2aF", "V2aR"):
        eq, idle = run_pool(sched, rk, days_all)
        r = eq.pct_change().dropna()
        out.append(f"{'无' if rk is None else rk}: 终值{float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} 闲置{idle:.0%}")
    print(" | ".join(out))
