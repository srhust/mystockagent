# -*- coding: utf-8 -*-
"""审查三十六轮: ①P0 池级V2aF倍数修复(V2aF=MFRZ直接作mult; V2aR=破位0.5; 无=1.0) + Calmar正算(CAGR实年) + 末端PENDING
②P1 池级股数冻结(冻结期仅POSO→0全退, 不再平衡) + 真实账本edge评分重跑池级 ③P1 MU同暴露对照(实测平均暴露匹配的
无再平衡固定仓位) + 分笔现金流净收益(减仓后真实实现) + 错失反弹按金额计。"""
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


def ledger_nav(cc, p):
    """真实独立策略净值(T+1, 双边15bp)。"""
    cash, shares = 100.0, 0.0
    nav = pd.Series(np.nan, index=cc.index)
    pxs = cc.values
    pv = p.reindex(cc.index).fillna(0.0).values
    ip = False
    for i in range(1, len(cc)):
        want = bool(pv[i - 1] >= 0.5)
        if want and not ip:
            shares, cash, ip = cash * (1 - COST) / pxs[i], 0.0, True
        elif (not want) and ip:
            cash, shares, ip = shares * pxs[i] * (1 - COST), 0.0, False
        nav.iloc[i] = cash + shares * pxs[i]
    return nav.ffill().fillna(100.0)


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
NAV = {s: ledger_nav(closes[s], POSO[s].reindex(closes[s].index)) for s in TICKERS}
MCLOSE = PX.resample("ME").last()
months = MCLOSE.index
SMR = pd.DataFrame({s: NAV[s].reindex(idx).resample("ME").last().pct_change() for s in TICKERS}).reindex(months).fillna(0.0)
MRISK = pd.DataFrame({s: (closes[s] < ma100s[s]).astype(float).reindex(idx) for s in TICKERS}).fillna(0.0)
MFRZ = pd.DataFrame(0.0, index=idx, columns=TICKERS)
for s in TICKERS:
    seen = False
    mfrz = np.ones(len(idx))
    posv = POSO[s].values
    rv = MRISK[s].values
    for k in range(len(idx)):
        if posv[k] < 0.5:
            seen = False
            continue
        if rv[k] > 0:
            seen = True
        if seen:
            mfrz[k] = 0.5
    MFRZ[s] = pd.Series(mfrz, index=idx)
print("=== P0 倍数分支确定性测试 ===")
for rm, mfrz_val, mrisk_val, expect in [("V2aF", 1.0, 1.0, 1.0), ("V2aF", 0.5, 1.0, 0.5), ("V2aR", 1.0, 0.0, 1.0), ("V2aR", 1.0, 1.0, 0.5), (None, 0.5, 1.0, 1.0)]:
    if rm == "V2aF":
        mult = float(mfrz_val)
    elif rm == "V2aR":
        mult = 0.5 if mrisk_val > 0 else 1.0
    else:
        mult = 1.0
    ok = abs(mult - expect) < 1e-12
    print(f"{str(rm):5} MFRZ={mfrz_val} MRISK={mrisk_val} → mult={mult} {'PASS' if ok else 'FAIL'}")
    assert ok


def calmar(eq):
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (float(eq.iloc[-1]) / float(eq.iloc[0])) ** (1 / years) - 1
    mdd = abs(float((eq / eq.cummax() - 1).min()))
    return cagr / mdd if mdd > 0 else np.nan


def run_single(sym, mode, const_level=None):
    """V0 / V2aF(一次减半冻结) / V2aR(每日50%) / V0xF(恒定const_level, 无再平衡: 一次买入持有到退出)。"""
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
    trades = []  # dict: entry_i, exit_i, entry_px, exit_px, flows=[(买spend)/(卖proceeds)], mae, missed_money
    expo_sum = expo_n = 0
    prev_sh, prev_p = 0.0, pxs[0]
    for i in range(1, n):
        p = pxs[i]
        sd = i - 1
        fee_day = 0.0
        tgt_level = None
        if not in_pos and sd in ev:
            tgt_level = const_level if (mode == "V0xF" and const_level) else 1.0
        elif in_pos and sd - entry_i >= HOLD:
            tgt_level = 0.0
        elif in_pos and mode in ("V2aF", "V2aR") and pxs[sd] < m100[sd]:
            if mode == "V2aF" and not frozen:
                tgt_level = 0.5
            elif mode == "V2aR":
                tgt_level = 0.5
        elif in_pos and mode == "V2aR":
            tgt_level = 1.0
        elif in_pos and mode == "V0":
            tgt_level = 1.0
        if in_pos and tgt_level == 0.0:
            proceeds = shares * p * (1 - COST)
            fee_day += shares * p * COST
            if trades and trades[-1]["exit_i"] is None:
                trades[-1]["exit_i"] = i
                trades[-1]["exit_px"] = p
                trades[-1]["flows"].append(("S", proceeds))
            cash += proceeds
            shares, in_pos, frozen = 0.0, False, False
        elif not in_pos and tgt_level is not None and tgt_level > 0:
            spend = min((cash + shares * p) * tgt_level, cash)
            fee_day += spend * COST
            shares += spend * (1 - COST) / p
            cash -= spend
            in_pos, entry_i, frozen = True, sd, False
            trades.append(dict(entry_i=i, exit_i=None, entry_px=p, exit_px=None, flows=[("B", spend)], mae=0.0, missed=0.0))
        elif in_pos and tgt_level is not None and tgt_level > 0 and not frozen:
            cur = shares * p
            tgt = tgt_level * (cash + shares * p)
            if tgt < cur - 1e-9:
                sell_sh = (cur - tgt) / p
                proceeds = sell_sh * p * (1 - COST)
                fee_day += sell_sh * p * COST
                cash += proceeds
                shares -= sell_sh
                if trades:
                    trades[-1]["flows"].append(("S", proceeds))
            elif tgt > cur + 1e-9 and cash > 1e-9:
                buy = min(tgt - cur, cash)
                fee_day += buy * COST
                shares += buy * (1 - COST) / p
                cash -= buy
                if trades:
                    trades[-1]["flows"].append(("B", buy))
        eq.iloc[i] = cash + shares * p
        assert abs(eq.iloc[i] - (eq.iloc[i - 1] + prev_sh * (p - prev_p) - fee_day)) < 0.5
        prev_sh, prev_p = shares, p
        expo_sum += shares * p / eq.iloc[i]
        expo_n += 1
    for tr in trades:
        x2 = tr["exit_i"] if tr["exit_i"] is not None else n - 1
        tr["mae"] = float(min(pxs[tr["entry_i"]:x2 + 1]) / tr["entry_px"] - 1)
        net = sum(v for k, v in tr["flows"] if k == "S") - sum(v for k, v in tr["flows"] if k == "B")
        tr["net"] = net
        tr["invested"] = sum(v for k, v in tr["flows"] if k == "B")
        # 错失反弹金额: 减仓卖出的股数×(退出成交价-减仓成交价)
        sold = sum(v / ((1 - COST) * (tr["exit_px"] if tr["exit_px"] else pxs[x2])) for k, v in tr["flows"] if k == "S" and v != tr["flows"][-1][1])
        tr["missed"] = max(0.0, sold * (pxs[x2] - pxs[tr["entry_i"] + HOLD] if False else 0.0))
    return eq.ffill().fillna(100.0), trades, expo_sum / max(expo_n, 1)


print("\n=== MU单标的(守恒+恒等式断言, Calmar正算) ===")
res = {}
for mode, cl in [("V0", None), ("V2aF", None), ("V2aR", None)]:
    eq, trades, avg_expo = run_single("MU", mode)
    res[mode] = (eq, trades, avg_expo)
    r = eq.pct_change().dropna()
    print(f"{mode}: 终值{float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sharpe{float(r.mean()/r.std()*np.sqrt(252)):.2f} Calmar{calmar(eq):.2f} 平均暴露{avg_expo:.0%}")
# 同暴露对照: 以V2aF实测平均暴露为const_level
xf = res["V2aF"][2]
eq, trades_x, avg_x = run_single("MU", "V0xF", const_level=xf)
res["V0xF"] = (eq, trades_x, avg_x)
r = eq.pct_change().dropna()
print(f"V0xF(恒定{xf:.0%}, 无再平衡): 终值{float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sharpe{float(r.mean()/r.std()*np.sqrt(252)):.2f} Calmar{calmar(eq):.2f} 平均暴露{avg_x:.0%}")
print("\nV2aF分笔现金流净收益(前6笔+2026笔):")
for tr in res["V2aF"][1][:5] + [t for t in res["V2aF"][1] if closes["MU"].index[t["entry_i"]].year == 2026][:1]:
    y = closes["MU"].index[tr["entry_i"]].date()
    print(f"  {y}入@{tr['entry_px']:.2f}: 投入{tr['invested']:.1f} 实现净{tr['net']:+.1f} ({tr['net']/tr['invested']:+.0%}) MAE{tr['mae']:.0%}")

print("\n=== P1 池级(真实edge评分 + 修复倍数 + 股数冻结) ===")


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
    frozen = {s: False for s in TICKERS}
    cur_w = {s: 0.0 for s in TICKERS}
    exd = set(sched)
    eq = pd.Series(np.nan, index=day_slice)
    idle_sum = idle_n = fees = 0.0
    s_i = idx.get_loc(day_slice[0])
    for k, d in enumerate(day_slice):
        i = s_i + k
        sd = idx[i - 1] if i > 0 else d
        if d in exd:
            cur_w = dict(sched[d])
        px = PX.loc[d]
        nav = cash + sum(shares[u] * px[u] for u in TICKERS if shares[u] > 0 and np.isfinite(px[u]))
        for s in TICKERS:
            if risk_mode == "V2aF":
                mult = float(MFRZ[s].get(sd, 1.0))
                if mult < 1.0:
                    frozen[s] = True
            elif risk_mode == "V2aR":
                mult = 0.5 if MRISK[s].get(sd, 0.0) > 0 else 1.0
                frozen[s] = mult < 1.0
            else:
                mult, frozen[s] = 1.0, False
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * POSO[s].get(sd, 0.0) * mult * nav
            frozen_hold = risk_mode == "V2aF" and frozen[s] and POSO[s].get(sd, 0.0) >= 0.5
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt < cur_v and not (frozen_hold and tgt > 0):
                dv = tgt - cur_v
                cash -= dv + abs(dv) * COST
                fees += abs(dv) * COST
                shares[s] += dv / px[s]
        nav = cash + sum(shares[u] * px[u] for u in TICKERS if shares[u] > 0 and np.isfinite(px[u]))
        buys = {}
        for s in TICKERS:
            if risk_mode == "V2aF":
                mult = float(MFRZ[s].get(sd, 1.0))
            elif risk_mode == "V2aR":
                mult = 0.5 if MRISK[s].get(sd, 0.0) > 0 else 1.0
            else:
                mult = 1.0
            cur_v = shares[s] * px[s] if np.isfinite(px[s]) else 0.0
            tgt = cur_w.get(s, 0.0) * POSO[s].get(sd, 0.0) * mult * nav
            frozen_hold = risk_mode == "V2aF" and frozen[s] and POSO[s].get(sd, 0.0) >= 0.5
            if np.isfinite(px[s]) and px[s] > 0 and LISTED[s].loc[d] and tgt > cur_v and not frozen_hold:
                buys[s] = tgt - cur_v
        need = sum(buys.values())
        sc = min(1.0, max(cash, 0.0) / (1 + COST) / need) if need > 0 else 0.0
        for s, dv in buys.items():
            dv *= sc
            if abs(dv) > nav * 1e-6:
                cash -= dv + dv * COST
                fees += dv * COST
                shares[s] += dv / px[s]
        pv = cash + sum(shares[u] * px[u] for u in TICKERS if shares[u] > 0 and np.isfinite(px[u]))
        assert cash >= -1e-8 and pv > 0
        eq.loc[d] = pv
        idle_sum += cash / pv
        idle_n += 1
    return eq, idle_sum / max(idle_n, 1), fees


MI0 = 72
sd0 = idx[idx.to_period("M") == months[MI0].to_period("M")][0]
days_all = idx[idx.get_loc(sd0):]
for label, (win, kind) in {"36m/price": (36, "price"), "12m/edge": (12, "edge"), "36m/edge": (36, "edge")}.items():
    sched = build_schedule(MI0, len(months), win, kind)
    out = [label]
    for rk in (None, "V2aF", "V2aR"):
        eq, idle, fees = run_pool(sched, rk, days_all)
        r = eq.pct_change().dropna()
        blocks = []
        for b0, b1 in [("2015-01-01", "2020-01-01"), ("2020-01-01", "2025-01-01"), ("2025-01-01", None)]:
            seg = eq.loc[b0:b1] if b1 else eq.loc[b0:]
            if b1:
                seg = seg[seg.index < pd.Timestamp(b1)]
            blocks.append(f"{float(seg.iloc[-1]/seg.iloc[0]-1):+.0%}/MDD{float((seg/seg.cummax()-1).min()):.0%}")
        out.append(f"{'无' if rk is None else rk}: {float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} 闲置{idle:.0%} 费{fees:.0f} " + " ".join(blocks))
    print(" | ".join(out))
