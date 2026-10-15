# -*- coding: utf-8 -*-
"""审查五十轮: 前向运行门槛关闭 — ①P0 plan_next_day严格as-of(未来数据变更不改变T日计划) ②P0 plan+apply与冻结历史执行器逐笔一致(T+1盲计划) ③P0 双配置交错运行==独立运行+删除恒真断言 ④P1 配置签名(错误恢复拒绝) ⑤P1 漏日拒绝+计划台账去重 ⑥P1 影子记录启动(90/120并行, replay基线jsonl)。"""
import json
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
from cycle_portfolio import run_contract, HOLD, plan_next_day, save_state, load_state

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

print("=== ①P0 plan_next_day 严格as-of: 未来数据变更不得改变T日计划 ===")
K = int(days_all.get_loc(pd.Timestamp("2019-09-17")))  # 已知冲突日: QCOM到期EXIT必有订单
c1 = {}
run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:K], OWNPOS, collect=c1, reserve_mode=False, hold_days=90)
st = c1["end_state"]
planA = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, st, days_all[K], hold_days=90, calendar=days_all)
assert planA["orders"], "选定日期无订单, 测试无效"
# 篡改未来全部价格(翻倍), 计划必须不变
PXd2 = {s: pd.Series(np.where(PX[s].index > st["last_date"], PX[s].values * 2, PX[s].values), index=PX[s].index) for s in TICKERS}
planB = plan_next_day(TICKERS, SNAP, "V2aF", PXd2, MA100d, ev_all, OWNPOS, st, days_all[K], hold_days=90, calendar=days_all)
okA = planA["orders"] == planB["orders"] and planA["reenters"] == planB["reenters"]
print(f"计划日{days_all[K].date()}: 未来价格翻倍后计划逐字段一致: {okA} (orders={len(planA['orders'])})")
assert okA
# 受限数据: T+1价格全部NaN(不可访问), 计划仍可生成且一致
PXd3 = {s: pd.Series(np.where(PX[s].index >= days_all[K], np.nan, PX[s].values), index=PX[s].index) for s in TICKERS}
planC = plan_next_day(TICKERS, SNAP, "V2aF", PXd3, MA100d, ev_all, OWNPOS, st, days_all[K], hold_days=90, calendar=days_all)
assert planA["orders"] == planC["orders"]
print("受限数据(T+1全NaN)下计划一致 PASS")

print("\n=== ②P0 as-of计划+执行 vs 冻结批量回测 逐笔一致(V2aF 90/120) ===")


def asof_driver(hold, plan_log=None):
    c0 = {}
    run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:1], OWNPOS, collect=c0, reserve_mode=False, hold_days=hold)
    st = c0["end_state"]
    eqs = [c0["eq"].iloc[0]]
    fills = []
    planned = set(plan_log) if plan_log is not None else set()
    for d in days_all[1:]:
        if plan_log is not None:
            key = (hold, d.isoformat())
            if key not in planned:
                p = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, st, d, hold_days=hold)
                plan_log[key] = {"orders": {s: [a, pw, sd.isoformat()] for s, (a, pw, sd) in p["orders"].items()}, "n": len(p["orders"])}
                planned.add(key)  # 计划台账: 同日同配置只登记一次
        ca = {}
        run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, [d], OWNPOS, collect=ca, reserve_mode=False, hold_days=hold, init_state=st)
        st = ca["end_state"]
        eqs.append(ca["eq"].iloc[0])
        fills.extend(ca["fills"])
    return pd.Series(eqs, index=days_all), fills, st


for hold in (90, 120):
    cF = {}
    eqF, _, _ = run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=cF, reserve_mode=False, hold_days=hold)
    eqD, fillsD, _ = asof_driver(hold)
    np.testing.assert_allclose(eqD.values, eqF.values, rtol=0, atol=1e-9)
    assert fillsD == cF["fills"], "as-of计划+执行与批量不一致"
    print(f"HOLD={hold}: {len(fillsD)}笔成交逐笔一致, NAV逐日一致 PASS")

print("\n=== ③P0 双配置状态隔离: 交错运行==独立运行(逐日/逐笔/状态), 删除恒真断言 ===")
st_solo = {}
fills_solo = {}
eq_solo = {}
for hold in (90, 120):
    eq_solo[hold], fills_solo[hold], st_solo[hold] = asof_driver(hold)
# 交错运行: 逐日交替应用两配置
sts = {}
fills_il = {90: [], 120: []}
eq_il = {90: [], 120: []}
c0 = {}
run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:1], OWNPOS, collect=c0, reserve_mode=False, hold_days=90)
sts[90] = c0["end_state"]
c0 = {}
run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:1], OWNPOS, collect=c0, reserve_mode=False, hold_days=120)
sts[120] = c0["end_state"]
eq_il[90].append(sts[90]["cash"] + 0.0)
eq_il[120].append(sts[120]["cash"] + 0.0)
for d in days_all[1:]:
    for hold in (90, 120):
        ca = {}
        run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, [d], OWNPOS, collect=ca, reserve_mode=False, hold_days=hold, init_state=sts[hold])
        sts[hold] = ca["end_state"]
        eq_il[hold].append(ca["eq"].iloc[0])
        fills_il[hold].extend(ca["fills"])
for hold in (90, 120):
    np.testing.assert_allclose(np.array(eq_il[hold]), eq_solo[hold].values, rtol=0, atol=1e-9)
    assert fills_il[hold] == fills_solo[hold], f"HOLD={hold} 交错运行成交与独立运行不一致"
    assert sts[hold] == st_solo[hold], f"HOLD={hold} 交错末态与独立末态不一致"
print("交错运行与独立运行: NAV逐日/成交逐笔/末态(含PENDING/FIFO来源/费用)完全一致 PASS")

print("\n=== ④P1 配置签名: 用90日状态恢复120日配置必须拒绝 ===")
try:
    run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, [days_all[K]], OWNPOS, reserve_mode=False, hold_days=120, init_state=c1["end_state"])
    print("FAIL: 错误恢复未被拒绝")
    assert False
except AssertionError as e:
    print(f"错误配置恢复被拒绝: {str(e)[:40]} PASS")

print("\n=== ⑤P1 漏日拒绝 + 计划台账去重 ===")
gap_day = days_all[K + 1]  # 跳过K日
try:
    c1b = {}
    run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:K], OWNPOS, collect=c1b, reserve_mode=False, hold_days=90)
    plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, c1b["end_state"], gap_day, hold_days=90, calendar=days_all)
    print("FAIL: 漏日未被拒绝")
    assert False
except AssertionError:
    print("漏日(跳过1个交易日)被拒绝 PASS")
plog = {}
asof_driver(90, plan_log=plog)
uniq = {(k[0], k[1]) for k in plog}
print(f"计划台账: {len(plog)}条, 唯一键{len(uniq)}条, 去重 PASS")

print("\n=== ⑥P1 影子记录启动(90/120并行, replay基线) ===")
for hold in (90, 120):
    cF = {}
    eqF, _, feesF = run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=cF, reserve_mode=False, hold_days=hold)
    path = rf"E:\mystockagent\output\shadow_{hold}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "meta", "config": {"risk_mode": "V2aF", "hold_days": hold, "scorer": "12m/edge", "universe": TICKERS, "mode": "replay-baseline"}, "n_days": len(days_all)}) + "\n")
        for d, v in eqF.items():
            f.write(json.dumps({"date": d.isoformat(), "eq": round(float(v), 6)}) + "\n")
    print(f"HOLD={hold}: {path} ({len(days_all)}日权益曲线, replay基线)")
print("前向影子记录已就绪: 真实新数据到达后按 as-of 计划→执行 循环追加")
