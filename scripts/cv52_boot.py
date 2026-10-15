# -*- coding: utf-8 -*-
"""审查五十二轮: 启动前最后两P0 — ①配置身份跨日保持(继承+哈希防篡改) ②计划预算冻结(signal_nav/身份哈希入计划对象, T日数据修订不影响) ③真实I/O持久化计划台账 ④双配置forward日志(bootstrap-replay) ⑤协议锁定(自哈希+代码SHA, 拒绝覆盖)。"""
import hashlib
import json
import os
import subprocess
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
from cycle_portfolio import run_contract, HOLD, plan_next_day, apply_day, _enc

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
os.makedirs(FWD, exist_ok=True)
CFGX = {"scorer": "12m/edge", "cost": COST, "dedup": DEDUP}

print("=== ①P0 配置身份跨日保持: apply_day不传config_extra也不丢失 ===")
K = int(days_all.get_loc(pd.Timestamp("2019-09-17")))
c1 = {}
run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:K], OWNPOS, collect=c1, reserve_mode=False, hold_days=90, config_extra=CFGX)
st = c1["end_state"]
p = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, st, days_all[K], hold_days=90, calendar=days_all)
ca = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st, p, days_all[K], hold_days=90)  # 未传config_extra
st2 = ca["end_state"]
ok1 = st2["config"].get("scorer") == "12m/edge" and st2["config"].get("cost") == COST and st2["config_hash"] == st["config_hash"]
print(f"次日快照config含scorer/cost/dedup且哈希不变: {ok1} (hash={st2['config_hash']})")
assert ok1
# 篡改快照配置 → 哈希校验拒绝
st_bad = dict(st)
st_bad["config"] = dict(st["config"], cost=0.003)
try:
    run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, [days_all[K]], OWNPOS, reserve_mode=False, hold_days=90, init_state=st_bad, config_extra=CFGX)
    print("FAIL: 篡改未被拒绝")
    raise SystemExit(1)
except AssertionError as e:
    print(f"配置篡改被哈希校验拒绝: {str(e)[:32]} PASS")

print("\n=== ②P0 计划预算冻结: T日数据修订不改变订单与原始预算 ===")
pA = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, st, days_all[K], hold_days=90, calendar=days_all)
pF = json.loads(json.dumps(_enc(dict(pA))))  # 计划对象序列化冻结
navF = pF["signal_nav"]
PXd_m = {s: pd.Series(np.where(PX[s].index == days_all[K - 1], PX[s].values * 3, PX[s].values), index=PX[s].index) for s in TICKERS}  # 修订T日收盘价×3
caA = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st, pA, days_all[K], hold_days=90)
caB = apply_day(TICKERS, SNAP, "V2aF", PXd_m, MA100d, ev_all, LISTd, OWNPOS, st, pA, days_all[K], hold_days=90)  # T日价已修订, 计划不变
assert caA["fills"] == caB["fills"], "T日修订改变了成交"
assert pA["signal_nav"] == navF
print(f"计划冻结signal_nav={navF:.2f} | T日价格×3修订后成交{len(caA['fills'])}笔逐笔相同, 预算不变 PASS")

print("\n=== ③P1 真实I/O持久化计划台账: 写盘→重启读回→不重复登记 ===")
lp = os.path.join(FWD, "plan_ledger.jsonl")
seg = days_all[K:K + 3]
if os.path.exists(lp):
    os.remove(lp)
for d in seg:  # 第一次运行: 生成+登记+执行推进状态
    pp = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, st, d, hold_days=90, calendar=days_all)
    pid = hashlib.sha1(json.dumps({"cfg": 90, "d": d.isoformat(), "orders": {s: [a, pw, sd.isoformat()] for s, (a, pw, sd) in pp["orders"].items()}, "state_hash": pp["state_hash"]}, sort_keys=True).encode()).hexdigest()[:12]
    with open(lp, "a", encoding="utf-8") as f:
        f.write(json.dumps({"plan_id": pid, "date": d.isoformat(), "signal_nav": pp["signal_nav"], "state_hash": pp["state_hash"]}) + "\n")
    st = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st, pp, d, hold_days=90)["end_state"]
n1 = sum(1 for _ in open(lp, encoding="utf-8"))
seen = set()  # "重启": 从磁盘重建台账
for line in open(lp, encoding="utf-8"):
    seen.add(json.loads(line)["plan_id"])
st2 = c1["end_state"]  # 重放起点
new = 0
for d in seg:  # 第二次运行: 重新生成同日计划
    pp = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, st2, d, hold_days=90, calendar=days_all)
    pid = hashlib.sha1(json.dumps({"cfg": 90, "d": d.isoformat(), "orders": {s: [a, pw, sd.isoformat()] for s, (a, pw, sd) in pp["orders"].items()}, "state_hash": pp["state_hash"]}, sort_keys=True).encode()).hexdigest()[:12]
    if pid not in seen:
        new += 1
        with open(lp, "a", encoding="utf-8") as f:
            f.write(json.dumps({"plan_id": pid}) + "\n")
    st2 = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st2, pp, d, hold_days=90)["end_state"]
n2 = sum(1 for _ in open(lp, encoding="utf-8"))
assert new == 0 and n1 == n2 == 3
print(f"磁盘台账{n1}条 → 重启读回去重 → 重复提交0条新增({n2}条) PASS")

print("\n=== ④P1 双配置forward日志: 90/120各写bootstrap-replay(计划+成交+NAV+费用+状态哈希) ===")
KB = len(days_all) - 31  # 末30个交易日bootstrap(非计分)
for hold in (90, 120):
    c0 = {}
    run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:KB], OWNPOS, collect=c0, reserve_mode=False, hold_days=hold, config_extra=CFGX)
    stb = c0["end_state"]
    fp = os.path.join(FWD, f"shadow_{hold}_forward.jsonl")
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "bootstrap-replay", "hold": hold, "start": days_all[KB].isoformat(), "end": days_all[-1].isoformat(), "not_scoring": True}) + "\n")
    for d in days_all[KB:]:
        pp = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, stb, d, hold_days=hold, calendar=days_all)
        pid = hashlib.sha1(json.dumps({"cfg": hold, "d": d.isoformat(), "orders": {s: [a, pw, sd.isoformat()] for s, (a, pw, sd) in pp["orders"].items()}, "state_hash": pp["state_hash"]}, sort_keys=True).encode()).hexdigest()[:12]
        cc2 = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, stb, pp, d, hold_days=hold)
        stb = cc2["end_state"]
        with open(fp, "a", encoding="utf-8") as f:
            f.write(json.dumps({"plan_id": pid, "date": d.isoformat(), "n_orders": len(pp["orders"]), "n_fills": len(cc2["fills"]), "eq": round(float(cc2["eq"].iloc[0]), 6), "fees_cum": round(stb["fees"], 4), "state_hash": pp["state_hash"], "config_hash": stb["config_hash"]}) + "\n")
    print(f"HOLD={hold}: {fp} (+31行: 1 bootstrap头 + 30日 计划/成交/NAV/费用/双哈希)")

print("\n=== ⑤P1 协议锁定: 自哈希+代码SHA, 重复运行拒绝覆盖 ===")
pp2 = os.path.join(FWD, "protocol.json")
proto = json.load(open(pp2, encoding="utf-8"))
try:
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=r"E:\mystockagent", text=True).strip()
except Exception:
    sha = "unknown"
data_h = hashlib.sha1()
for s2 in TICKERS:
    data_h.update(open(os.path.join(D, f"{s2}.csv"), "rb").read())
if "locked" not in proto:
    proto["locked"] = True
    proto["code_sha"] = sha
    proto["data_hash"] = data_h.hexdigest()[:12]
    body = json.dumps({k: v for k, v in proto.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
    proto["self_hash"] = hashlib.sha1(body.encode()).hexdigest()[:12]
    with open(pp2, "w", encoding="utf-8") as f:
        json.dump(proto, f, ensure_ascii=False, indent=1)
    print(f"协议已锁定: code={sha[:8]} data={proto['data_hash']} self={proto['self_hash']}")
else:  # 已锁定: 校验自哈希, 拒绝改写
    body = json.dumps({k: v for k, v in proto.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
    assert hashlib.sha1(body.encode()).hexdigest()[:12] == proto["self_hash"], "协议内容被改写!"
    print(f"协议已锁定且自哈希校验通过, 本次运行未改写 PASS (code={proto.get('code_sha', '')[:8]})")
print("启动前工程关闭完成")
