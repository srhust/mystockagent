# -*- coding: utf-8 -*-
"""审查二十一轮: C v4冻结网格 — 先修两P0再实验。
P0-A修复: 月初决策只读上一完整月月末(泄漏清除)。
P0-B修复: 仅估值实际持仓(零持仓不参与NaN乘), 无有效价禁止开仓, 买入受现金硬约束, 每日断言。
评分命名: price_momentum_score(价格动量) / strategy_edge_score(策略绩效)分开。
网格: {12,36,60}月窗 × {orig滚动120日, X2 MA100, X3 25%跟踪, H混合(MA100破位∨25%回撤, 退出后仅新事件再入)}。
稳健性: A多起点(2015/2017/2019/2021), B去最佳单月/前三月归因, D全指标(CAGR/Sharpe/Calmar/恢复期/换手/现金占比)。"""
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
POOL = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]
COST = 0.0015
closes, raw_ev = {}, {}
for sym in POOL:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    ev = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    raw_ev[sym] = ev
    closes[sym] = c
idx = pd.concat(closes.values()).index.unique().sort_values()
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in POOL}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx).notna() for s in POOL})  # 有效行情日


def pos_variant(sym, mode):
    """四种目标仓位: orig=滚动120日窗口(现状); X2/X3/H=事件入场+规则退出+仅新事件再入(120日事件去重)。"""
    c = closes[sym]
    ev = raw_ev[sym].values
    n = len(c)
    if mode == "orig":
        return pd.Series(ev.astype(float), index=c.index).rolling(120, min_periods=1).max().astype(float)
    evd, last = set(), -999
    for i in range(n):
        if ev[i] and i - last >= 120:
            evd.add(i)
            last = i
    ma100 = c.rolling(100).mean().values
    pos = np.zeros(n)
    in_pos = False
    peak = np.nan
    for i in range(n):
        x = c.values[i]
        if not in_pos and i in evd:
            in_pos = True
            peak = x
        elif in_pos:
            peak = max(peak, x)
            if mode == "X2":
                dead = x < ma100[i]
            elif mode == "X3":
                dead = x < peak * 0.75
            else:
                dead = (x < ma100[i]) or (x < peak * 0.75)
            if dead:
                in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=c.index)


POSV = {mode: pd.DataFrame({s: pos_variant(s, mode).reindex(idx) for s in POOL}).fillna(0.0) for mode in ("orig", "X2", "X3", "H")}
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index
STRAT_MRET = {mode: pd.DataFrame({s: POSV[mode][s].shift(1).fillna(0) * PX[s].pct_change().fillna(0) for s in POOL}).resample("ME").apply(lambda x: (1 + x).prod() - 1) for mode in ("orig",)}


def price_momentum_score(sym, mi, win):
    """P0-A修复: 决策月months[mi]只读months[mi-1]及以前的月末。窗口内归一化。"""
    w = MCLOSE[sym].iloc[max(0, mi - win):mi].dropna()  # 截至上月月末
    if len(w) < win or w.iloc[0] <= 0:
        return np.nan
    eqw = w / w.iloc[0]
    mdd = float((eqw / eqw.cummax() - 1).min())
    return float(eqw.iloc[-1]) / abs(mdd) if mdd else np.nan


def strategy_edge_score(sym, mi, win, mode="orig"):
    w = STRAT_MRET[mode][sym].iloc[max(0, mi - win):mi]
    if len(w) < win:
        return np.nan
    eqw = (1 + w).cumprod()
    mdd = float((eqw / eqw.cummax() - 1).min())
    return float(eqw.iloc[-1]) / abs(mdd) if mdd else np.nan


def build_schedule(mi0, mi1, win, mode, score_type):
    P = POSV[mode]
    sched = {}
    for mi in range(mi0, mi1):
        m = months[mi]
        t_day = idx[idx.to_period("M") == m.to_period("M")][0]
        scores, act = {}, {}
        for s in POOL:
            sc = price_momentum_score(s, mi, win) if score_type == "price" else strategy_edge_score(s, mi, win, "orig")
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


def run_ledger(weight_schedule, start_day, P, budget=100.0):
    cash = float(budget)
    shares = {s: 0.0 for s in POOL}
    cur_w = {s: 0.0 for s in POOL}
    exec_days = set(weight_schedule)
    days = idx[idx.get_loc(start_day):]
    eq = pd.Series(np.nan, index=days)
    expo = pd.Series(np.nan, index=days)
    traded = 0.0
    start_i = idx.get_loc(start_day)
    for k, d in enumerate(days):
        i = start_i + k
        sd = idx[i - 1] if i > 0 else d
        if d in exec_days:
            cur_w = dict(weight_schedule[d])
        px = PX.loc[d]
        mv = sum(shares[s] * px[s] for s in POOL if shares[s] > 0 and np.isfinite(px[s]))  # P0-B: 仅持仓估值
        port_val = cash + mv
        assert np.isfinite(port_val) and port_val > 0, f"{d}: 净值无效"
        for s in POOL:
            tgt = cur_w.get(s, 0.0) * P[s].get(sd, 0.0) * port_val
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d]:
                dv = tgt - cur_v
                if dv > 0:
                    dv = min(dv, max(cash, 0.0) / (1 + COST))  # 现金硬约束(含费)
                if abs(dv) > port_val * 1e-6:
                    cash -= dv + abs(dv) * COST
                    shares[s] += dv / px[s]
                    traded += abs(dv)
        mv = sum(shares[s] * px[s] for s in POOL if shares[s] > 0 and np.isfinite(px[s]))
        port_val = cash + mv
        assert cash >= -1e-8, f"{d}: 现金为负 {cash}"
        assert np.isfinite(port_val) and port_val > 0
        eq.loc[d] = port_val
        expo.loc[d] = mv / port_val
    return eq, expo, traded


def stats(eq, expo, traded):
    r = eq.pct_change().dropna()
    yrs = len(r) / 252
    cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    dd = eq / eq.cummax() - 1
    mdd = float(dd.min())
    peak_t = dd.cummax()
    rec = 0
    cur = 0
    for v in (dd < -1e-9).values:
        cur = cur + 1 if v else 0
        rec = max(rec, cur)
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan
    calmar = cagr / abs(mdd) if mdd else np.nan
    return dict(final=float(eq.iloc[-1]), mdd=mdd, cagr=cagr, sharpe=sharpe, calmar=calmar,
                recovery_days=rec, cash_pct=float(1 - expo.mean()), turnover=traded / (float(eq.iloc[-1]) * yrs))


# ===== 网格: 统一起点(36月评分窗+12月缓冲 → months[48]起) =====
MI0, MI1 = 48, len(months)
start_day = idx[idx.to_period("M") == months[MI0].to_period("M")][0]
print(f"统一起点: {start_day.date()} | 评分泄漏修复: 决策月只读上月末及以前")
print("\n=== C v4 冻结网格 (3窗口×4退出, price_momentum_score) ===")
grid = []
for win in (12, 36, 60):
    for mode in ("orig", "X2", "X3", "H"):
        sched = build_schedule(MI0, MI1, win, mode, "price")
        eq, expo, traded = run_ledger(sched, start_day, POSV[mode])
        st = stats(eq, expo, traded)
        grid.append(dict(win=win, exit=mode, **st))
        print(f"{win}月/{mode}: 终值{st['final']:.1f} MDD{st['mdd']:.0%} CAGR{st['cagr']:.0%} Sharpe{st['sharpe']:.2f} Calmar{st['calmar']:.1f} 恢复{st['recovery_days']}日 现金{st['cash_pct']:.0%}")
pd.DataFrame(grid).to_csv(os.path.join(config.DATA_DIR, "cv4_grid.csv"), index=False)
base = [g for g in grid if g["win"] == 36 and g["exit"] == "orig"][0]
chal = [g for g in grid if g["win"] == 12 and g["exit"] == "H"][0]

print("\n=== 检验A: 多起点(冻结基准36月/orig vs 挑战12月/H) ===")
for yr in (2015, 2017, 2019, 2021):
    mi_s = next(i for i, m in enumerate(months) if m.year == yr and i >= 36 + 6)
    sd = idx[idx.to_period("M") == months[mi_s].to_period("M")][0]
    row = [str(yr)]
    for win, mode in ((36, "orig"), (12, "H")):
        sched = build_schedule(mi_s, len(months), win, mode, "price")
        eq, expo, traded = run_ledger(sched, sd, POSV[mode])
        st = stats(eq, expo, traded)
        row.append(f"{st['final']:.0f}/{st['mdd']:.0%}")
    print(f"起点{yr}: 基准终值/MDD={row[1]} | 挑战={row[2]}")

print("\n=== 检验B: 去最佳月归因(基准 vs 挑战) ===")
for win, mode in ((36, "orig"), (12, "H")):
    sched = build_schedule(MI0, MI1, win, mode, "price")
    eq, expo, traded = run_ledger(sched, start_day, POSV[mode])
    mr = eq.resample("ME").last().pct_change().dropna()
    top = mr.nlargest(5)
    tot_log = np.log1p(mr).sum()
    for k in (1, 3, 5):
        rest = np.log1p(mr.drop(top.index[:k])).sum()
        print(f"{win}月/{mode}: 去Top{k}月后累计收益 {np.expm1(rest):.0%} (全期{np.expm1(tot_log):.0%}); Top月: {[str(t.date())[:7] for t in top.index[:k]]}")
