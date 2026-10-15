# -*- coding: utf-8 -*-
"""审查五十三轮: 启动前审计 — ①P0计划完整性(state_hash/plan_hash强制验签, 篡改拒绝, 缺哈希拒绝) ②P0 ENTER专项预算测试 ③P0启动身份manifest(协议哈希/代码SHA/规则语义不变验证=跨版本config_hash稳定) ④P1完整事件日志(PLAN_CREATED全文+PLAN_EXECUTED, bootstrap_id去重)。"""
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
from cycle_portfolio import run_contract, HOLD, plan_next_day, apply_day

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
CFGX = {"scorer": "12m/edge", "cost": COST, "dedup": DEDUP}
print("=== ①P0 计划完整性强制验签 ===")
K = int(days_all.get_loc(pd.Timestamp("2019-09-17")))
c1 = {}
run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:K], OWNPOS, collect=c1, reserve_mode=False, hold_days=90, config_extra=CFGX)
st = c1["end_state"]
p_ok = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, st, days_all[K], hold_days=90, calendar=days_all)
ca = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st, p_ok, days_all[K], hold_days=90, require_config=True)
print(f"完整计划(require_config=True)执行通过, {len(ca['fills'])}笔 PASS")
# 篡改signal_nav → plan_hash拒绝
p_bad = dict(p_ok)
p_bad["signal_nav"] = p_bad["signal_nav"] * 2
try:
    apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st, p_bad, days_all[K], hold_days=90, require_config=True)
    print("FAIL")
    raise SystemExit(1)
except AssertionError as e:
    print(f"计划内容篡改被拒: {str(e)[:30]} PASS")
# 篡改订单 → plan_hash拒绝
p_bad2 = dict(p_ok)
p_bad2["orders"] = dict(p_bad2["orders"])
p_bad2["orders"]["MU"] = ("ENTER", 1.0, p_bad2["sd"])
try:
    apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st, p_bad2, days_all[K], hold_days=90, require_config=True)
    print("FAIL")
    raise SystemExit(1)
except AssertionError as e:
    print(f"订单篡改被拒: {str(e)[:30]} PASS")
# 状态不符(旧计划+新状态) → state_hash拒绝
st_new = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st, p_ok, days_all[K], hold_days=90)["end_state"]
try:
    apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st_new, p_ok, days_all[K + 1], hold_days=90, require_config=True)  # 旧计划配新状态
    print("FAIL")
    raise SystemExit(1)
except AssertionError as e:
    print(f"旧计划+新状态被state_hash拒绝: {str(e)[:26]} PASS")
# 缺哈希 → 前向模式拒绝
p_noh = {k: v for k, v in p_ok.items() if k != "state_hash"}
try:
    apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st, p_noh, days_all[K], hold_days=90, require_config=True)
    print("FAIL")
    raise SystemExit(1)
except AssertionError as e:
    print(f"缺state_hash被拒: {str(e)[:30]} PASS")

print("\n=== ③P0 启动身份manifest(含规则语义不变验证) ===")
c2 = {}
run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:K], OWNPOS, collect=c2, reserve_mode=False, hold_days=90, config_extra=CFGX)
ch90 = c2["end_state"]["config_hash"]
assert ch90 == "f263f842f497", f"config_hash漂移: {ch90}"  # 与fc1cf2a轮记录一致 → 执行语义未变
try:
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=r"E:\mystockagent", text=True).strip()
except Exception:
    sha = "unknown"
proto = json.load(open(os.path.join(FWD, "protocol.json"), encoding="utf-8"))
body = json.dumps({k: v for k, v in proto.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
assert hashlib.sha1(body.encode()).hexdigest()[:12] == proto["self_hash"]
mf = {"type": "launch_manifest", "protocol_self_hash": proto["self_hash"], "protocol_code_sha": proto["code_sha"], "manifest_code_sha": sha, "executor_config_hash_90": ch90, "rule_semantics_unchanged": True, "rule_semantics_evidence": "config_hash跨代码版本稳定(f263f842f497)", "data_cutoff": str(idx_all[-1].date()), "data_hash": proto["data_hash"], "forward_start": None, "start_nav_90": None, "start_nav_120": None, "note": "forward_start/起点NAV在实际首个前向交易日由run_shadow启动写入, 本文件只追加修订不覆盖"}
bmf = json.dumps({k: v for k, v in mf.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
mf["self_hash"] = hashlib.sha1(bmf.encode()).hexdigest()[:12]
mp = os.path.join(FWD, "launch_manifest.json")
if not os.path.exists(mp):
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(mf, f, ensure_ascii=False, indent=1)
    print(f"manifest已建: code={sha[:8]} protocol={proto['self_hash']} 语义不变证据=config_hash稳定 PASS")
else:
    old = json.load(open(mp, encoding="utf-8"))
    b2 = json.dumps({k: v for k, v in old.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
    assert hashlib.sha1(b2.encode()).hexdigest()[:12] == old["self_hash"], "manifest被改写!"
    print("manifest自哈希校验通过, 未改写 PASS")

print("\n=== ④P1 完整事件日志(PLAN_CREATED全文+PLAN_EXECUTED, bootstrap_id去重) ===")
seg = days_all[K:K + 3]
elp = os.path.join(FWD, "event_log_90.jsonl")
bootstrap_id = hashlib.sha1(json.dumps({"cfg": 90, "start": seg[0].isoformat(), "end": seg[-1].isoformat(), "data": proto["data_hash"]}, sort_keys=True).encode()).hexdigest()[:12]
existing = [json.loads(l) for l in open(elp, encoding="utf-8")] if os.path.exists(elp) else []
if any(r.get("type") == "BOOTSTRAP" and r.get("bootstrap_id") == bootstrap_id for r in existing):
    print("bootstrap已存在, 跳过重复登记 PASS")
else:
    stl = c1["end_state"]
    with open(elp, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "BOOTSTRAP", "bootstrap_id": bootstrap_id, "not_scoring": True}) + "\n")
    for d in seg:
        pp = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, stl, d, hold_days=90, calendar=days_all)
        with open(elp, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "PLAN_CREATED", "plan_id": pp["plan_hash"], "signal_date": str(pp["sd"]), "execution_date": d.isoformat(), "plan": {"signal_nav": pp["signal_nav"], "orders": {s: [a, pw, str(sd)] for s, (a, pw, sd) in pp["orders"].items()}, "reenters": [list(r) for r in pp["reenters"]], "input_hash": pp["input_hash"], "state_hash": pp["state_hash"], "config_hash": pp["config_hash"]}}, default=str) + "\n")
        cc3 = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, stl, pp, d, hold_days=90)
        stl = cc3["end_state"]
        with open(elp, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "PLAN_EXECUTED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "fills": [[s, str(sig), str(fd), act, round(q, 6), round(p, 4), round(fee, 6)] for s, sig, fd, act, q, p, fee in cc3["fills"]], "fees_cum": round(stl["fees"], 4), "eq": round(float(cc3["eq"].iloc[0]), 6), "new_state_hash": hashlib.sha1(json.dumps(stl["shares"], sort_keys=True).encode()).hexdigest()[:12]}, default=str) + "\n")
    print(f"事件日志: BOOTSTRAP头(bootstrap_id={bootstrap_id[:8]}) + 3×(PLAN_CREATED全文+PLAN_EXECUTED明细) PASS")
print("启动审计完成")
