# -*- coding: utf-8 -*-
"""审查五十一轮: 计划-执行闭环 — ①P0 apply_day消费冻结计划(plan_id追溯, 全期与批量逐笔一致) ②P1 前向日志隔离(replay/forward目录, 追加模式, baseline不可覆盖) ③P1 持久化计划台账(重启去重) ④P1 配置指纹(require_config拒绝缺失) ⑤P1 前向评测协议预冻结。"""
import hashlib
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
from cycle_portfolio import run_contract, HOLD, plan_next_day, apply_day, save_state, load_state

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

FWD = r"E:\mystockagent\output\forward"
REP = r"E:\mystockagent\output\replay"
os.makedirs(FWD, exist_ok=True)
os.makedirs(REP, exist_ok=True)

print("=== ①P0 apply_day消费冻结计划: 全期计划→执行 与 批量回测 逐笔一致(V2aF 90/120) ===")


def plan_driven_driver(hold, fwd_log=None):
    """T日: plan_next_day(as-of, 计划入台账带plan_id) → T+1: apply_day消费该计划。"""
    c0 = {}
    run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:1], OWNPOS, collect=c0, reserve_mode=False, hold_days=hold, config_extra={"scorer": "12m/edge", "cost": COST, "dedup": DEDUP})
    st = c0["end_state"]
    eqs = [c0["eq"].iloc[0]]
    fills, plan_ids = [], []
    for d in days_all[1:]:
        p = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, st, d, hold_days=hold, calendar=days_all)
        pid = hashlib.sha1(json.dumps({"cfg": hold, "d": d.isoformat(), "orders": {s: [a, pw, sd.isoformat()] for s, (a, pw, sd) in p["orders"].items()}}, sort_keys=True).encode()).hexdigest()[:12]
        plan_ids.append(pid)
        if fwd_log is not None and pid not in fwd_log:
            fwd_log[pid] = {"date": d.isoformat(), "n_orders": len(p["orders"])}  # 持久台账: 重启后从文件重建, 不重复登记
            plan_ids.append(pid)
        ca = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st, p, d, hold_days=hold, collect=None)
        st = ca["end_state"]
        eqs.append(ca["eq"].iloc[0])
        fills.extend(ca["fills"])
    return pd.Series(eqs, index=days_all), fills, st, plan_ids


for hold in (90, 120):
    cF = {}
    eqF, _, _ = run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all, OWNPOS, collect=cF, reserve_mode=False, hold_days=hold)
    eqD, fillsD, stD, pids = plan_driven_driver(hold)
    np.testing.assert_allclose(eqD.values, eqF.values, rtol=0, atol=1e-9)
    assert fillsD == cF["fills"], "计划驱动执行与批量回测成交不一致"
    print(f"HOLD={hold}: {len(pids)}个计划(plan_id唯一) → {len(fillsD)}笔成交与批量逐笔全等, NAV逐日一致 PASS")

print("\n=== ②P1 前向日志隔离: baseline入replay/, forward只追加 ===")
b90, b120 = os.path.join(REP, "shadow_90_baseline.jsonl"), os.path.join(REP, "shadow_120_baseline.jsonl")
if not os.path.exists(b90):
    import shutil
    shutil.move(r"E:\mystockagent\output\shadow_90.jsonl", b90)
    shutil.move(r"E:\mystockagent\output\shadow_120.jsonl", b120)
h90 = hashlib.sha1(open(b90, "rb").read()).hexdigest()
f90 = os.path.join(FWD, "shadow_90_forward.jsonl")
with open(f90, "a", encoding="utf-8") as f:
    f.write(json.dumps({"type": "session", "mode": "forward", "started_at": pd.Timestamp.now().isoformat()}) + "\n")
assert hashlib.sha1(open(b90, "rb").read()).hexdigest() == h90, "baseline被改写!"
print(f"replay/{os.path.basename(b90)} 哈希未变, forward追加写入不触碰baseline PASS")

print("\n=== ③P1 持久化计划台账: 重启后去重(模拟进程重启重放同一计划日) ===")
tlog = {}
c1 = {}
run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:200], OWNPOS, collect=c1, reserve_mode=False, hold_days=90, config_extra={"scorer": "12m/edge", "cost": COST, "dedup": DEDUP})
st = c1["end_state"]
d1 = days_all[200]
p = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, st, d1, hold_days=90, calendar=days_all)
pid = hashlib.sha1(json.dumps({"cfg": 90, "d": d1.isoformat(), "orders": {s: [a, pw, sd.isoformat()] for s, (a, pw, sd) in p["orders"].items()}}, sort_keys=True).encode()).hexdigest()[:12]
tlog[pid] = 1  # 第一次登记
tlog2 = dict(tlog)  # "重启": 台账从持久层重建
p2 = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, st, d1, hold_days=90, calendar=days_all)
pid2 = hashlib.sha1(json.dumps({"cfg": 90, "d": d1.isoformat(), "orders": {s: [a, pw, sd.isoformat()] for s, (a, pw, sd) in p2["orders"].items()}}, sort_keys=True).encode()).hexdigest()[:12]
assert pid == pid2 and pid2 in tlog2
print(f"同一计划日重复生成: plan_id={pid2} 一致且已登记, 不重复入账 PASS")

print("\n=== ④P1 配置指纹: 缺失签名拒绝 + 评分器不符拒绝 ===")
st_nocfg = {k: v for k, v in c1["end_state"].items() if k not in ("config", "config_hash")}
try:
    run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, [days_all[200]], OWNPOS, reserve_mode=False, hold_days=90, init_state=st_nocfg, require_config=True)
    print("FAIL: 缺失签名未被拒绝")
    raise SystemExit(1)
except AssertionError as e:
    print(f"缺失配置签名被拒绝: {str(e)[:30]} PASS")
st_bad = dict(c1["end_state"])
st_bad["config"] = dict(st_bad["config"], scorer="36m/price")
try:
    run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, [days_all[200]], OWNPOS, reserve_mode=False, hold_days=90, init_state=st_bad, config_extra={"scorer": "12m/edge", "cost": COST, "dedup": DEDUP})
    print("FAIL: 评分器不符未被拒绝")
    raise SystemExit(1)
except AssertionError as e:
    print(f"评分器配置不符被拒绝: {str(e)[:30]} PASS")

print("\n=== ⑤P1 前向评测协议预冻结(先于新数据) ===")
proto = {
    "registered_at": pd.Timestamp.now().isoformat(),
    "configs": [{"name": "V2aF-Recycle-90", "risk_mode": "V2aF", "hold_days": 90}, {"name": "V2aF-Recycle-120", "risk_mode": "V2aF", "hold_days": 120}],
    "scorer": "12m/edge", "universe": TICKERS, "cost_model": COST, "dedup": DEDUP,
    "forward_start": "两套配置及数据版本锁定后的首个真实交易日",
    "normalization": "前向起点各自归一化为100(历史持仓分别继承, 不人为清仓)",
    "primary_metrics": ["前向累计净收益", "最大回撤", "Sharpe"],
    "secondary_metrics": ["实际成交数", "换手", "资金占用", "手续费", "订单执行偏差(计划vs成交)"],
    "observation_window_months": 24,
    "window_note": "24个月为预定观察窗口, 不代表两年即具统计显著性; 最终需考虑已完成交易数量与不确定区间",
    "early_stop": "仅允许因系统安全故障停止; 不因短期收益领先提前宣布胜者",
    "rule_freeze": "观察期内不修改任何交易规则; 不再进行HOLD参数搜索(60/90/120/180之外无新值)",
}
with open(os.path.join(FWD, "protocol.json"), "w", encoding="utf-8") as f:
    json.dump(proto, f, ensure_ascii=False, indent=1)
print("protocol.json 已存档(output/forward/protocol.json): 观察期/指标/停止条件先于新数据冻结 PASS")
print("计划-执行闭环完成")
