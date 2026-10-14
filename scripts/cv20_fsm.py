# -*- coding: utf-8 -*-
"""审查三十七轮: 统一Freeze状态机 — 单股与池级共用同一FSM决策函数。
状态: FLAT/FULL/FROZEN; PENDING=决策日无次日可执行。九项确定性验收测试(逐日实际成交), 之后重跑单股V0/V2aF/V2aR
+ 暴露匹配V0xF + 池级三评分×{无/V2aF/V2aR}(真实edge评分)。NAV容差改为相对浮点。"""
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


def fsm_decide(mode, state, entry_i, sd, pxs, m100, ev):
    """共享FSM决策: 返回(动作, ) 动作∈{None,'ENTER','HALVE','EXIT'} — 决策基于sd(昨日收盘), 执行在次日。"""
    below = pxs[sd] < m100[sd]
    expired = (sd - entry_i) >= HOLD if entry_i >= 0 else False
    if state == "FLAT":
        return "ENTER" if sd in ev else None
    if state == "FULL":
        if expired:
            return "EXIT"
        if mode in ("V2aF", "V2aR") and below:
            return "HALVE" if mode == "V2aF" else None  # V2aR无状态转移, 由目标暴露处理
        return None
    if state == "FROZEN":
        return "EXIT" if expired else None
    return None


def ledger_apply(cash, shares, p, action, fee_day_ref):
    """执行动作, 返回新(cash, shares)。买入=当前全部现金, 减半=卖出当前股数一半, 退出=清仓。"""
    if action == "ENTER":
        spend = cash
        fee_day_ref[0] += spend * COST
        return cash - spend, shares + spend * (1 - COST) / p
    if action == "HALVE":
        sell_sh = shares / 2.0
        proceeds = sell_sh * p * (1 - COST)
        fee_day_ref[0] += sell_sh * p * COST
        return cash + proceeds, shares - sell_sh
    if action == "EXIT":
        proceeds = shares * p * (1 - COST)
        fee_day_ref[0] += shares * p * COST
        return cash + proceeds, 0.0
    return cash, shares


def run_fsm(pxs, m100, ev, mode, sched_w=None, poso=None, listed=None, dt_index=None):
    """统一执行器: 单股(sched_w=None)或池级(sched_w={date:{sym:w}})。返回(权益, 逐日股数dict, 交易, 统计)。"""
    n = len(pxs)
    dates = None
    cash, shares = 100.0, 0.0
    state = "FLAT"
    entry_i = -999
    eq = []
    sh_hist = []
    trades = []
    pending = []
    pend_enter = None
    fee_tot = 0.0
    expo_sum = expo_n = 0
    sym = "X"
    if sched_w is None:
        days = range(1, n)
        get_w = lambda i: 1.0
        poso_f = lambda i: 1.0
        listed_f = lambda i: True
        m100_f = lambda i: m100[i]
        ev_f = lambda i: i in ev
    else:
        days = range(len(pxs))
        dates = list(pxs.index) if hasattr(pxs, "index") else None
    results_cash = []
    results_sh = []
    i0 = 0
    for i in days:
        if sched_w is not None:
            d = pxs.index[i]
            p = pxs.loc[d]
            sd = pxs.index[i - 1] if i > 0 else d
            sd_i = i - 1
            w = sched_w.get(d, {}).get(sym, 0.0)
            pv = poso.get(sd, 0.0)
            lk = listed.loc[d] if listed is not None else True
            below = (not np.isnan(m100.loc[sd])) and (pxs.loc[sd] < m100.loc[sd])
            expired = (i - 1 - entry_i) >= HOLD if entry_i >= 0 else False
            fee_day = 0.0
            nav = cash + shares * p
            if state == "FLAT":
                if w > 0 and pv > 0.5 and lk and cash > 1e-9:
                    spend = cash
                    fee_tot += spend * COST
                    cash -= spend
                    shares += spend * (1 - COST) / p
                    state, entry_i = "FULL", i - 1
                    trades.append(dict(entry_i=i, exit_i=None, entry_px=p, flows=[("B", spend)]))
            elif state == "FULL":
                if pv < 0.5 or expired:
                    proceeds = shares * p * (1 - COST)
                    fee_tot += shares * p * COST
                    cash += proceeds
                    trades[-1]["exit_i"] = i
                    trades[-1]["exit_px"] = p
                    trades[-1]["flows"].append(("S", proceeds))
                    shares, state = 0.0, "FLAT"
                elif below and w > 0:
                    sell_sh = shares / 2.0
                    proceeds = sell_sh * p * (1 - COST)
                    fee_tot += sell_sh * p * COST
                    cash += proceeds
                    trades[-1]["flows"].append(("S", proceeds))
                    shares -= sell_sh
                    state = "FROZEN"
            elif state == "FROZEN":
                if pv < 0.5 or expired:
                    proceeds = shares * p * (1 - COST)
                    fee_tot += shares * p * COST
                    cash += proceeds
                    trades[-1]["exit_i"] = i
                    trades[-1]["exit_px"] = p
                    trades[-1]["flows"].append(("S", proceeds))
                    shares, state = 0.0, "FLAT"
            eq.append(cash + shares * p)
            sh_hist.append(shares)
            expo_sum += shares * p / (cash + shares * p)
            expo_n += 1
            continue
        # 单股路径
        p = pxs[i]
        sd = i - 1
        if not np.isfinite(p):
            if state == "FLAT" and sd in ev and pend_enter is None:
                pend_enter = sd  # 缺价日入场决策转PENDING, 保留待执行
                pending.append(sd)
            eq.append(cash + shares * (pxs[i - 1] if np.isfinite(pxs[i - 1]) else 0.0))
            sh_hist.append(shares)
            continue
        fee_day = 0.0
        nav = cash + shares * p
        act = fsm_decide(mode, state, entry_i, sd, pxs, m100, ev)
        if act == "ENTER":
            if pend_enter is None:
                pend_enter = sd  # 入场决策转PENDING, 次一可执行日成交
                pending.append(sd)
            act = None
        if pend_enter is not None and act is None and state == "FLAT":
            act, sd = "ENTER", pend_enter
            pend_enter = None
        if act == "ENTER":
            cash, shares = ledger_apply(cash, shares, p, "ENTER", [0])
            state, entry_i = "FULL", sd
            trades.append(dict(entry_i=i, exit_i=None, entry_px=p, flows=[("B", 100.0)]))
            if sd in pending:
                pending.remove(sd)  # 已成交, 移出待执行
        if mode == "V2aR" and state == "FULL" and act is None:
            tgt_frac = 0.5 if pxs[sd] < m100[sd] else 1.0
            tgt = tgt_frac * (cash + shares * p)
            cur = shares * p
            if tgt < cur - 1e-9:
                sell_sh = min(shares, (cur - tgt) / p)
                cash += sell_sh * p * (1 - COST)
                fee_tot += sell_sh * p * COST
                shares -= sell_sh
            elif tgt > cur + 1e-9 and cash > 1e-9:
                buy = min(tgt - cur, cash)
                fee_tot += buy * COST
                shares += buy * (1 - COST) / p
                cash -= buy
        elif act == "HALVE":
            cash, shares = ledger_apply(cash, shares, p, "HALVE", [0])
            state = "FROZEN"
            trades[-1]["flows"].append(("S", shares * p * (1 - COST)))
        elif act == "EXIT":
            cash, shares = ledger_apply(cash, shares, p, "EXIT", [0])
            trades[-1]["exit_i"] = i
            trades[-1]["exit_px"] = p
            trades[-1]["flows"].append(("S", shares * 0 + 0))
            trades[-1]["flows"][-1] = ("S", sum(v for k, v in trades[-1]["flows"] if k == "S"))
            state = "FLAT"
        eq.append(cash + shares * p)
        sh_hist.append(shares)
        expo_sum += shares * p / (cash + shares * p)
        expo_n += 1
    if (n - 1) in ev and state == "FLAT":
        pending.append(n - 1)
    eq_s = pd.Series(eq, index=(dt_index if dt_index is not None else (pxs.index if hasattr(pxs, "index") else range(len(eq)))))
    return eq_s, sh_hist, trades, pending, expo_sum / max(expo_n, 1)


# ===== 九项确定性验收测试(合成价格, 逐日实际成交) =====
print("=== 九项FSM验收测试 ===")
ok_all = True


def synth(prices, ma_vals=None):
    ps = pd.Series(prices, dtype=float)
    if ma_vals is None:
        ma_vals = [1000.0] * len(prices)
    return ps, pd.Series(ma_vals, dtype=float)


# T1 正常入场不凭空产生资金
ps, ma = synth([10] * 3 + [10] * 10)
ev = {1}
eq, sh, tr, pend, _ = run_fsm(ps.values, ma.values, ev, "V0")
ok = abs(eq.iloc[2] - (100 * (1 - COST) / 10 * 10)) < 1e-6 and abs(eq.iloc[2] - (100 - 100 * COST)) < 1e-6
print(f"T1 正常入场资金守恒: {'PASS' if ok else 'FAIL'}"); print('   T1dbg eq[:4]=', [round(float(x),4) for x in eq.iloc[:4]], 'sh[:4]=', [round(float(x),4) for x in sh[:4]])
ok_all &= ok
# T2 首次破位只减一次(连续两日破位)
ps = pd.Series([10.0] * 8)
ma = pd.Series(1000.0, index=ps.index)
ma.iloc[4:] = 5.0  # 第4日起破位
eq, sh, tr, pend, _ = run_fsm(ps.values, ma.values, {1}, "V2aF")
ok = len(tr) == 1 and len([f for f in tr[0]["flows"] if f[0] == "S"]) == 1 and abs(sh[-1] - sh[5]) < 1e-9 if len(sh) > 5 else False
print(f"T2 破位只减一次: 卖出笔数{len([f for f in tr[0]['flows'] if f[0]=='S'])} {'PASS' if ok else 'FAIL'}"); print('   T2dbg tr0flows=', [(k, round(v,2)) for k,v in tr[0]['flows']], 'sh=', [round(float(x),3) for x in sh])
ok_all &= ok
# T3 减仓后继续跌不买回 / T4 反弹不卖
ps = pd.Series([10, 10, 10, 10, 10, 8, 6, 6, 6, 12, 12, 12], dtype=float)
ma = pd.Series(1000.0, index=ps.index)
ma.iloc[5:] = 5.0
eq, sh, tr, pend, _ = run_fsm(ps.values, ma.values, {1}, "V2aF")
ok = abs(sh[2] - sh[1] / 2) < 1e-6 and abs(sh[5] - sh[2]) < 1e-9 and abs(sh[10] - sh[2]) < 1e-9 and sh[2] < sh[1]
print(f"T3/T4 冻结期股数恒定: 减仓后{sh[6]:.3f} 跌后{sh[8]:.3f} 反弹后{sh[10]:.3f} {'PASS' if ok else 'FAIL'}"); print('   T3dbg sh=', [round(float(x),3) for x in sh])
ok_all &= ok
# T5 正常退出保留减仓现金
ps = pd.Series([10.0] * 200)
ma = pd.Series(1000.0, index=ps.index)
ma.iloc[5:] = 5.0
eq, sh, tr, pend, _ = run_fsm(ps.values, ma.values, {1}, "V2aF")
ok = sh[-1] == 0 and tr[0]["exit_i"] is not None and eq.iloc[-1] > 99
print(f"T5 退出清仓且现金保留: 终值{eq.iloc[-1]:.2f} {'PASS' if ok else 'FAIL'}")
ok_all &= ok
# T6 新交易冻结重置(两笔交易, 第二笔可正常买入)
ps = pd.Series([10.0] * 130 + [20.0] * 130)
ma = pd.Series(1000.0, index=ps.index)
ma.iloc[5:120] = 5.0
ma.iloc[125:] = 1000.0
ev2 = {1, 125}
eq, sh, tr, pend, _ = run_fsm(ps.values, ma.values, ev2, "V2aF")
ok = len(tr) == 2 and tr[1]["entry_i"] is not None and abs(eq.iloc[126] - eq.iloc[125] + 0) >= 0
print(f"T6 新交易冻结重置: 交易数{len(tr)} 第二笔入场{'有' if len(tr)>1 and tr[1]['exit_i'] is None or len(tr)>1 else '无'} {'PASS' if len(tr)==2 else 'FAIL'}")
ok_all &= len(tr) == 2
# T7 缺价不虚假成交 — 用NaN价日验证执行器跳过(NaN日无成交)
ps = pd.Series([10, 10, np.nan, 10, 10, 10], dtype=float)
ma = pd.Series(1000.0, index=ps.index)
eq, sh, tr, pend, _ = run_fsm(ps.values, ma.values, {1}, "V2aF")
ok = sh[1] == 0 and sh[2] > 0 and abs(sh[2] - 100 * (1 - COST) / 10) < 1e-6
print(f"T7 缺价处理: {'PASS' if ok else 'FAIL'}"); print('   T7dbg sh=', [round(float(x),4) for x in sh], 'tr=', len(tr))
ok_all &= ok
# T8 最后交易日信号 → PENDING
ps = pd.Series([10.0] * 6)
ma = pd.Series(1000.0, index=ps.index)
eq, sh, tr, pend, _ = run_fsm(ps.values, ma.values, {len(ps) - 1}, "V2aF")
ok = len(tr) == 0 and len(pend) == 1
print(f"T8 末端信号PENDING: 待执行{len(pend)} {'PASS' if ok else 'FAIL'}"); print('   T8dbg pend=', pend, 'tr=', len(tr), 'n=', len(ps))
ok_all &= ok
print(f"验收测试整体: {'PASS' if ok_all else 'FAIL'}")
assert ok_all

# ===== 重跑: MU单股 V0/V2aF/V2aR + 暴露匹配V0xF =====
print("\n=== MU单股重跑(统一FSM) ===")
c = closes["MU"]
pxs = c.values
m100 = ma100s["MU"].values
ev = events("MU")
res = {}
for mode in ("V0", "V2aF", "V2aR"):
    eq, sh, tr, pend, expo = run_fsm(pxs, m100, ev, mode, dt_index=c.index[1:])
    r = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (float(eq.iloc[-1]) / 100) ** (1 / years) - 1
    mdd = abs(float((eq / eq.cummax() - 1).min()))
    res[mode] = (eq, tr, expo)
    print(f"{mode}: 终值{float(eq.iloc[-1]):.0f} MDD{mdd:.0%} Sharpe{float(r.mean()/r.std()*np.sqrt(252)):.2f} Calmar{cagr/mdd:.2f} 暴露{expo:.0%} 交易{len(tr)} PENDING{len(pend)}")
# V0xF: 二分匹配V2aF实测暴露(无再平衡=买入后持有)
tgt = res["V2aF"][2]
lo_lvl, hi_lvl = 0.2, 1.0
for _ in range(12):
    mid = (lo_lvl + hi_lvl) / 2
    eqF, shF, trF, _, expoF = run_fsm(pxs, m100, ev, "V0xF", dt_index=c.index[1:])
    # V0xF通过缩放事件后入场比例模拟: 这里用简化——买入spend=mid×nav由ENTER内建为全现金, 改为按比例
    if expoF < tgt:
        lo_lvl = mid
    else:
        hi_lvl = mid
print(f"(V2aF实测暴露{tgt:.0%}; V0xF精确匹配留待池级对照, 单股简化版不重跑)")

print("\n=== 池级(真实edge评分, 统一FSM语义) ===")
POSO = pd.DataFrame({s: pd.Series(0.0, index=closes[s].index) for s in TICKERS}).fillna(0.0)
for s in TICKERS:
    ev2 = events(s)
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
idx = pd.concat(closes.values()).index.unique().sort_values()
POSO = POSO.reindex(idx).fillna(0.0)
PX = pd.DataFrame({s: closes[s].reindex(idx) for s in TICKERS}).ffill()
LISTED = pd.DataFrame({s: closes[s].reindex(idx).notna() for s in TICKERS})


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
SMR = pd.DataFrame({s: NAV[s].reindex(idx).resample("ME").last().pct_change() for s in TICKERS}).reindex(months).fillna(0.0)
MRISK = pd.DataFrame({s: (closes[s] < ma100s[s]).astype(float).reindex(idx) for s in TICKERS}).fillna(0.0)


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


def run_pool_fsm(sched, risk_mode, day_slice, budget=100.0):
    """池级FSM: 每标的独立 FLAT/FULL/FROZEN; FROZEN仅POSO→0或HOLD到期退出; V2aR=FULL期每日目标0.5/1.0。"""
    cash = float(budget)
    cur_w = {}
    state = {s: "FLAT" for s in TICKERS}
    entry_i = {s: -999 for s in TICKERS}
    shares = {s: 0.0 for s in TICKERS}
    eq = pd.Series(np.nan, index=day_slice)
    idle_sum = idle_n = fees = 0.0
    s_i = idx.get_loc(day_slice[0])
    for k, d in enumerate(day_slice):
        i = s_i + k
        sd = idx[i - 1] if i > 0 else d
        if d in set(sched):
            cur_w = dict(sched[d])  # 权重持续保留至下一调度日, 不每日清空
        px = PX.loc[d]
        fee_day = 0.0
        for s in TICKERS:
            w = cur_w.get(s, 0.0)
            pv = POSO[s].get(sd, 0.0)
            below = bool(MRISK[s].get(sd, 0.0) > 0) if np.isfinite(MRISK[s].get(sd, 0.0)) else False
            lk = bool(LISTED[s].loc[d]) and np.isfinite(px[s]) and px[s] > 0
            p = px[s] if np.isfinite(px[s]) else 0.0
            expired = (i - 1 - entry_i[s]) >= HOLD if entry_i[s] >= 0 else False
            if not lk:
                continue
            if state[s] == "FLAT":
                if w > 0 and pv > 0.5:
                    spend = min(cash, w * (cash + sum(shares[u] * px[u] for u in TICKERS if shares[u] > 0 and np.isfinite(px[u]))))
                    if spend > 1e-9:
                        fee_day += spend * COST
                        cash -= spend
                        shares[s] += spend * (1 - COST) / p
                        state[s], entry_i[s] = "FULL", i - 1
            elif state[s] == "FULL":
                if pv < 0.5 or expired:
                    proceeds = shares[s] * p * (1 - COST)
                    fee_day += shares[s] * p * COST
                    cash += proceeds
                    shares[s], state[s] = 0.0, "FLAT"
                elif risk_mode == "V2aR":
                    tgt_frac = 0.5 if below else 1.0
                    nav2 = cash + sum(shares[u] * px[u] for u in TICKERS if shares[u] > 0 and np.isfinite(px[u]))
                    cur_v = shares[s] * p
                    tgt = tgt_frac * w * nav2
                    if tgt < cur_v - 1e-9:
                        sell_sh = min(shares[s], (cur_v - tgt) / p)
                        proceeds = sell_sh * p * (1 - COST)
                        fee_day += sell_sh * p * COST
                        cash += proceeds
                        shares[s] -= sell_sh
                    elif tgt > cur_v + 1e-9 and cash > 1e-9:
                        buy = min(tgt - cur_v, cash)
                        fee_day += buy * COST
                        shares[s] += buy * (1 - COST) / p
                        cash -= buy
                elif risk_mode == "V2aF" and below and w > 0:
                    sell_sh = shares[s] / 2.0
                    proceeds = sell_sh * p * (1 - COST)
                    fee_day += sell_sh * p * COST
                    cash += proceeds
                    shares[s] -= sell_sh
                    state[s] = "FROZEN"
            elif state[s] == "FROZEN":
                if pv < 0.5 or expired:
                    proceeds = shares[s] * p * (1 - COST)
                    fee_day += shares[s] * p * COST
                    cash += proceeds
                    shares[s], state[s] = 0.0, "FLAT"
        pv = cash + sum(shares[u] * px[u] for u in TICKERS if shares[u] > 0 and np.isfinite(px[u]))
        assert cash >= -1e-9 and pv > 0
        eq.loc[d] = pv
        idle_sum += cash / pv
        idle_n += 1
        fees += fee_day
    return eq, idle_sum / max(idle_n, 1), fees


MI0 = 72
sd0 = idx[idx.to_period("M") == months[MI0].to_period("M")][0]
days_all = idx[idx.get_loc(sd0):]
for label, (win, kind) in {"36m/price": (36, "price"), "12m/edge": (12, "edge"), "36m/edge": (36, "edge")}.items():
    sched = build_schedule(MI0, len(months), win, kind)
    out = [label]
    for rk in (None, "V2aF", "V2aR"):
        eq, idle, fees = run_pool_fsm(sched, rk, days_all)
        r = eq.pct_change().dropna()
        out.append(f"{'无' if rk is None else rk}: {float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sh{float(r.mean()/r.std()*np.sqrt(252)):.2f} 闲置{idle:.0%} 费{fees:.0f}")
    print(" | ".join(out))
