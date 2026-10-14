# -*- coding: utf-8 -*-
"""审查二十二轮: ①账本修复(先卖后买+顺序不变性断言) ②退出消融单因素化(全变体冻结同一去重事件列表与入场, 仅退出不同:
orig=限时120日, X2=MA100破位, X3=25%跟踪, H=MA100∨25%) ③网格在12/36/60窗口均可计算的公共起点重跑
④多起点改为固定5年窗的四组准独立样本(2015-20/2017-22/2019-24/2021-26)×2×2因子 ⑤分块自助法(12m/H−36m/orig月收益差)。"""
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

D = os.path.join(config.DATA_DIR, "mu")
COST = 0.0015
closes, raw_ev = {}, {}
for sym in ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    raw_ev[sym] = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    closes[sym] = c
POOL = list(closes)
idx = pd.concat(closes.values()).index.unique().sort_values()
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in POOL}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx).notna() for s in POOL})
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index


def pos_frozen(sym, mode):
    """P0-2修复: 统一去重事件(120日), 统一入场(事件日为目标仓位日, T+1成交由账本执行), 仅退出规则不同。
    orig=入场后120日限时退出; X2=收盘<MA100; X3=收盘<入场后峰值×0.75; H=X2∨X3。退出后仅新事件再入。"""
    c = closes[sym]
    ev = raw_ev[sym].values
    n = len(c)
    evd, last = [], -999
    for i in range(n):
        if ev[i] and i - last >= 120:
            evd.append(i)
            last = i
    evd = set(evd)
    ma100 = c.rolling(100).mean().values
    pxs = c.values
    pos = np.zeros(n)
    in_pos = False
    entry_i = peak = np.nan
    for i in range(n):
        if not in_pos and i in evd:
            in_pos, entry_i, peak = True, i, pxs[i]
        elif in_pos:
            peak = max(peak, pxs[i])
            if mode == "orig":
                dead = i - entry_i >= 120
            elif mode == "X2":
                dead = pxs[i] < ma100[i]
            elif mode == "X3":
                dead = pxs[i] < peak * 0.75
            else:
                dead = (pxs[i] < ma100[i]) or (pxs[i] < peak * 0.75)
            if dead:
                in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=c.index)


POSV = {m: pd.DataFrame({s: pos_frozen(s, m).reindex(idx) for s in POOL}).fillna(0.0) for m in ("orig", "X2", "X3", "H")}


def trailing_score(sym, mi, win):
    """审查二十一轮口径: 决策月months[mi]只读mi-1及以前。price_momentum_score。"""
    w = MCLOSE[sym].iloc[max(0, mi - win):mi].dropna()
    if len(w) < win or w.iloc[0] <= 0:
        return np.nan
    eqw = w / w.iloc[0]
    mdd = float((eqw / eqw.cummax() - 1).min())
    return float(eqw.iloc[-1]) / abs(mdd) if mdd else np.nan


def build_schedule(mi0, mi1, win, mode):
    P = POSV[mode]
    sched = {}
    for mi in range(mi0, mi1):
        m = months[mi]
        t_day = idx[idx.to_period("M") == m.to_period("M")][0]
        scores, act = {}, {}
        for s in POOL:
            sc = trailing_score(s, mi, win)
            if np.isfinite(sc):
                scores[s] = sc
                act[s] = bool(P[s].reindex(idx[:idx.get_loc(t_day) + 1]).iloc[-1] >= 0.5)
        elig = {s: sc for s, sc in scores.items() if act.get(s)}
        w = {}
        if elig:
            rank = sorted(elig, key=elig.get, reverse=True)
            w = {rank[0]: 0.6}
            if len(rank) > 1:
                w[rank[1]] = 0.4
        exec_day = idx[idx.get_loc(t_day) + 1] if idx.get_loc(t_day) + 1 < len(idx) else t_day
        sched[exec_day] = w
    return sched


def run_ledger(weight_schedule, day_slice, P, order, budget=100.0):
    """P0-1修复: 先全部减仓/卖出(释放现金), 再按可用现金买入。order仅用于平铺循环顺序(不变性验证)。"""
    cash = float(budget)
    shares = {s: 0.0 for s in POOL}
    cur_w = {s: 0.0 for s in POOL}
    exec_days = set(weight_schedule)
    eq = pd.Series(np.nan, index=day_slice)
    expo = pd.Series(np.nan, index=day_slice)
    start_i = idx.get_loc(day_slice[0])
    for k, d in enumerate(day_slice):
        i = start_i + k
        sd = idx[i - 1] if i > 0 else d
        if d in exec_days:
            cur_w = dict(weight_schedule[d])
        px = PX.loc[d]
        for s in order:  # 第一遍: 卖出/减仓
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * P[s].get(sd, 0.0) * (cash + sum(shares[u] * px[u] for u in POOL if shares[u] > 0 and np.isfinite(px[u])))
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt < cur_v:
                dv = tgt - cur_v
                cash -= dv + abs(dv) * COST
                shares[s] += dv / px[s]
        mv0 = sum(shares[s] * px[s] for s in POOL if shares[s] > 0 and np.isfinite(px[s]))
        port_val = cash + mv0
        buys = {}
        for s in order:  # 第二遍: 买入(需求汇总后按比例分配 → 顺序不变)
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * P[s].get(sd, 0.0) * port_val
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt > cur_v:
                buys[s] = tgt - cur_v
        need = sum(buys.values())
        scale = min(1.0, max(cash, 0.0) / (1 + COST) / need) if need > 0 else 0.0
        for s, dv in buys.items():
            dv *= scale
            if abs(dv) > port_val * 1e-6:
                cash -= dv + dv * COST
                shares[s] += dv / px[s]
        mv = sum(shares[s] * px[s] for s in POOL if shares[s] > 0 and np.isfinite(px[s]))
        port_val = cash + mv
        assert cash >= -1e-8 and np.isfinite(port_val) and port_val > 0, f"{d}: 账本断言失败"
        eq.loc[d] = port_val
        expo.loc[d] = mv / port_val
    return eq, expo


def stats(eq, expo):
    r = eq.pct_change().dropna()
    yrs = len(r) / 252
    cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    mdd = float((eq / eq.cummax() - 1).min())
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan
    return dict(final=float(eq.iloc[-1]), mdd=mdd, cagr=cagr, sharpe=sharpe, cash=float(1 - expo.mean()))


# ===== 网格: 公共起点(60月窗+12缓冲 → months[72]) =====
MI0 = 72
sd0 = idx[idx.to_period("M") == months[MI0].to_period("M")][0]
print(f"公共起点(12/36/60窗均可计算): {sd0.date()}")
print("\n=== 修正网格(冻结事件单因素退出, 先卖后买账本) ===")
rows = []
for win in (12, 36, 60):
    for mode in ("orig", "X2", "X3", "H"):
        sched = build_schedule(MI0, len(months), win, mode)
        eq, expo = run_ledger(sched, idx[idx.get_loc(sd0):], POSV[mode], POOL)
        st = stats(eq, expo)
        rows.append(dict(win=win, exit=mode, **st))
        print(f"{win}月/{mode}: 终值{st['final']:.1f} MDD{st['mdd']:.0%} CAGR{st['cagr']:.0%} Sharpe{st['sharpe']:.2f} 现金{st['cash']:.0%}")
pd.DataFrame(rows).to_csv(os.path.join(config.DATA_DIR, "cv5_grid.csv"), index=False)

# ===== 顺序不变性测试 =====
sched_t = build_schedule(MI0, MI0 + 12, 12, "H")
e1, _ = run_ledger(sched_t, idx[idx.get_loc(sd0):idx.get_loc(sd0) + 800], POSV["H"], POOL)
e2, _ = run_ledger(sched_t, idx[idx.get_loc(sd0):idx.get_loc(sd0) + 800], POSV["H"], list(reversed(POOL)))
print(f"\n顺序不变性: 正序终值{float(e1.iloc[-1]):.6f} 逆序终值{float(e2.iloc[-1]):.6f} 差{abs(float(e1.iloc[-1]) - float(e2.iloc[-1])):.2e} {'PASS' if abs(float(e1.iloc[-1]) - float(e2.iloc[-1])) < 1e-6 else 'FAIL'}")

# ===== 多起点: 固定5年窗 × 2×2因子 =====
print("\n=== 检验A': 固定5年窗多起点 (12m/orig vs 12m/H vs 36m/orig vs 36m/H) ===")
fac = {}
for yr in (2015, 2017, 2019, 2021):
    mi_s = next(i for i, m in enumerate(months) if m.year == yr and i >= 48)
    mi_e = mi_s + 60
    sd = idx[idx.to_period("M") == months[mi_s].to_period("M")][0]
    days = idx[idx.get_loc(sd):idx.get_loc(sd) + 1260]
    line = [str(yr)]
    for win, mode in ((12, "orig"), (12, "H"), (36, "orig"), (36, "H")):
        sched = build_schedule(mi_s, mi_e, win, mode)
        eq, expo = run_ledger(sched, days, POSV[mode], POOL)
        st = stats(eq, expo)
        fac[(yr, win, mode)] = st
        line.append(f"{win}m/{mode}:{st['final']:.0f}/{st['mdd']:.0%}")
    print(" | ".join(line))
print("\n因子化增量(各起点平均):")
for a, b, tag in [((12, "orig"), (36, "orig"), "窗口12−36(orig)"), ((12, "H"), (36, "H"), "窗口12−36(H)"),
                  ((12, "orig"), (12, "H"), "退出orig−H(12m)"), ((36, "orig"), (36, "H"), "退出orig−H(36m)")]:
    diffs = [fac[(y, a[0], a[1])]["final"] - fac[(y, b[0], b[1])]["final"] for y in (2015, 2017, 2019, 2021)]
    print(f"{tag}: {['%+.0f' % d for d in diffs]} 均值{np.mean(diffs):+.0f}")

# ===== 检验E: 分块自助法(主期间 12m/H − 36m/orig 月收益差) =====
sA = build_schedule(MI0, len(months), 12, "H")
sB = build_schedule(MI0, len(months), 36, "orig")
eA, _ = run_ledger(sA, idx[idx.get_loc(sd0):], POSV["H"], POOL)
eB, _ = run_ledger(sB, idx[idx.get_loc(sd0):], POSV["orig"], POOL)
dA = eA.resample("ME").last().pct_change().dropna()
dB = eB.resample("ME").last().pct_change().dropna()
diff = (dA - dB).values
rng = np.random.default_rng(42)
L, nIt = 6, 2000
means = []
for _ in range(nIt):
    smp = []
    while len(smp) < len(diff):
        st_ = rng.integers(0, len(diff))
        smp.extend(diff[st_:st_ + L])
    means.append(np.mean(smp[:len(diff)]))
lo, hi = np.percentile(means, [5, 95])
print(f"\n检验E: 12m/H−36m/orig 月均收益差 {diff.mean():+.3%}, 块自助90%CI [{lo:+.3%}, {hi:+.3%}] {'显著' if lo > 0 or hi < 0 else '不显著(含0)'}")
