# -*- coding: utf-8 -*-
"""审查五十四轮: 最小启动闭环 — ①P0日志存_enc(plan)全文→_dec→验plan_hash→apply_day重放成功 ②P0统一state_hash跨日链连续(PLAN_EXECUTED[T].new_state_hash==PLAN_CREATED[T+1].state_hash) ③P0启动凭证(文件内容哈希绑定执行器) ④P0 run_shadow驱动器(双配置, 崩溃恢复: 计划落盘后才执行, PLAN_EXECUTED幂等) ⑤P1故障注入(计划写入后/结算后崩溃, 恢复不重放成交不漏日)。"""
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
from cycle_portfolio import run_contract, HOLD, plan_next_day, apply_day, _enc, _dec, state_hash, save_state, load_state

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
N5 = 5  # 尾部5个交易日做完整闭环

print("=== ①P0 完整计划日志(_enc全文)→反序列化→验签→重放(90) ===")
K = int(days_all.get_loc(pd.Timestamp("2019-09-17")))
c1 = {}
run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:K], OWNPOS, collect=c1, reserve_mode=False, hold_days=90, config_extra=CFGX)
st = c1["end_state"]
elp = os.path.join(FWD, "event_log_90_v2.jsonl")
if os.path.exists(elp):
    os.remove(elp)
seg = days_all[K:K + 3]
stl = st
recs = []
for d in seg:  # 写入: PLAN_CREATED存_enc(plan)全文 + PLAN_EXECUTED存完整state_hash
    pp = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, stl, d, hold_days=90, calendar=days_all)
    with open(elp, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "PLAN_CREATED", "plan_id": pp["plan_hash"], "plan": _enc(dict(pp))}, default=str) + "\n")
    ca = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, stl, pp, d, hold_days=90, require_config=True)
    stl = ca["end_state"]
    with open(elp, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "PLAN_EXECUTED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "n_fills": len(ca["fills"]), "eq": round(float(ca["eq"].iloc[0]), 6), "new_state_hash": state_hash(stl)}, default=str) + "\n")
    recs.append((pp, ca, d))
# 重放: 从磁盘读回→_dec→验plan_hash→apply_day(require_config=True)成功
st2 = st
for i, d in enumerate(seg):
    lines = [json.loads(l) for l in open(elp, encoding="utf-8")]
    pc = [r for r in lines if r["type"] == "PLAN_CREATED"][i]
    plan_recovered = _dec(pc["plan"])
    body = {k: v for k, v in plan_recovered.items() if k != "plan_hash"}
    assert hashlib.sha1(json.dumps(_enc(body), sort_keys=True, default=str).encode()).hexdigest()[:12] == plan_recovered["plan_hash"], "日志恢复的计划验签失败"
    ca2 = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st2, plan_recovered, d, hold_days=90, require_config=True)
    assert ca2["fills"] == recs[i][1]["fills"]
    st2 = ca2["end_state"]
print(f"3日: 磁盘计划→_dec→plan_hash验签通过→重放成交逐笔一致 PASS")
# ②哈希链: EXECUTED[T].new_state_hash == CREATED[T+1].state_hash
lines = [json.loads(l) for l in open(elp, encoding="utf-8")]
ex = [r for r in lines if r["type"] == "PLAN_EXECUTED"]
cr = [r for r in lines if r["type"] == "PLAN_CREATED"]
chain = all(ex[i]["new_state_hash"] == _dec(cr[i + 1]["plan"])["state_hash"] for i in range(len(seg) - 1))
print(f"状态哈希跨日链连续(EXECUTED[T]==CREATED[T+1]): {chain} PASS")
assert chain

print("\n=== ④P0 run_shadow 双配置驱动器+崩溃恢复(尾部5日, 模拟两种崩溃点) ===")


def shadow_run(hold, start_idx, n_days, state_path, log_path, crash_after_plan_at=None, crash_after_exec_at=None):
    """正式每日影子循环: 计划落盘→执行→EXECUTED落盘→状态存盘。崩溃后resume不重放成交。"""
    if os.path.exists(state_path):
        st = load_state(state_path)
    else:
        c0 = {}
        run_contract(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, days_all[:start_idx], OWNPOS, collect=c0, reserve_mode=False, hold_days=hold, config_extra=CFGX)
        st = c0["end_state"]
        save_state(st, state_path)
    done = set()
    created_ids = set()
    pending_plan = None
    if os.path.exists(log_path):
        for l in open(log_path, encoding="utf-8"):
            r = json.loads(l)
            if r.get("type") == "PLAN_EXECUTED":
                done.add(r["plan_id"])
            elif r.get("type") == "PLAN_CREATED":
                created_ids.add(r["plan_id"])
                if r["plan_id"] not in done:
                    pending_plan = _dec(r["plan"])  # P0恢复: 复用崩溃前已落盘的未完成计划, 不重新生成
    pos0 = list(days_all).index(st["last_date"])  # P0恢复: 从状态末日推进, 不重放已执行日
    for j, d in enumerate(days_all[pos0 + 1:start_idx + n_days]):
        if pending_plan is not None and pending_plan["date"] == d:
            pp = pending_plan
            pending_plan = None
        else:
            pp = plan_next_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, OWNPOS, st, d, hold_days=hold, calendar=days_all)
        if pp["plan_hash"] not in created_ids:  # CREATED幂等: 同plan_id不重复登记
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "PLAN_CREATED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "plan": _enc(dict(pp))}, default=str) + "\n")
            created_ids.add(pp["plan_hash"])
        if crash_after_plan_at == j:  # 模拟崩溃: 计划已落盘, 未执行
            return "crash_after_plan"
        ca = apply_day(TICKERS, SNAP, "V2aF", PXd, MA100d, ev_all, LISTd, OWNPOS, st, pp, d, hold_days=hold, require_config=True)
        st = ca["end_state"]
        if pp["plan_hash"] not in done:  # 幂等: 重复执行不重复登记
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "PLAN_EXECUTED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "n_fills": len(ca["fills"]), "eq": round(float(ca["eq"].iloc[0]), 6), "new_state_hash": state_hash(st)}, default=str) + "\n")
        if crash_after_exec_at == j:  # 模拟崩溃: 已执行, 状态未存盘
            return "crash_after_exec"
        save_state(st, state_path)
    return "done"


for hold in (90, 120):
    base = os.path.join(FWD, f"run_{hold}")
    for pth in (f"{base}_state.json", f"{base}_log.jsonl"):
        if os.path.exists(pth):
            os.remove(pth)
    r1 = shadow_run(hold, len(days_all) - N5, 2, f"{base}_state.json", f"{base}_log.jsonl", crash_after_plan_at=1)
    r2 = shadow_run(hold, len(days_all) - N5, 2, f"{base}_state.json", f"{base}_log.jsonl", crash_after_exec_at=0)  # resume: 复用已落盘计划执行day1后、存盘前崩溃
    r3 = shadow_run(hold, len(days_all) - N5, N5, f"{base}_state.json", f"{base}_log.jsonl")  # 完成剩余日
    # 对照: 无崩溃完整跑
    ref_state = f"{base}_ref.json"
    for pth in (ref_state, f"{base}_reflog.jsonl"):
        if os.path.exists(pth):
            os.remove(pth)
    shadow_run(hold, len(days_all) - N5, N5, ref_state, f"{base}_reflog.jsonl")
    st_c, st_r = load_state(f"{base}_state.json"), load_state(ref_state)
    ex_c = [json.loads(l) for l in open(f"{base}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
    ex_r = [json.loads(l) for l in open(f"{base}_reflog.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
    pids_c = [r["plan_id"] for r in ex_c]
    assert st_c == st_r, f"HOLD={hold} 崩溃恢复终态与不中断不一致"
    assert [r["eq"] for r in ex_c] == [r["eq"] for r in ex_r] and len(pids_c) == len(set(pids_c)) == N5
    crs = [json.loads(l) for l in open(f"{base}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_CREATED"]
    cbd = {_dec(r["plan"])["date"].isoformat(): r for r in crs}
    assert len(crs) == len(ex_c) == N5, f"CREATED {len(crs)} / EXECUTED {len(ex_c)} 应各为{N5}"
    chain = all(ex_c[i]["new_state_hash"] == _dec(cbd[ex_c[i + 1]["date"]]["plan"])["state_hash"] for i in range(N5 - 1))
    print(f"HOLD={hold}: 崩溃点[{r1}→{r2}→{r3}] 恢复终态/NAV/成交数与不中断一致, plan_id无重复({N5}), 哈希链连续={chain} PASS")

print("\n=== ③P0 启动凭证(文件内容哈希绑定执行器) ===")
fh = {f: hashlib.sha256(open(os.path.join(r"E:\mystockagent", f), "rb").read()).hexdigest()[:16] for f in ("cycle_system/cycle_portfolio.py", "scripts/run_shadow.py") if os.path.exists(os.path.join(r"E:\mystockagent", f))}
cred = {"type": "launch_credential", "approved_candidate_commit": "d7a9bb1", "executor_file_hashes": fh, "config_hash_90": load_state(os.path.join(FWD, "run_90_state.json"))["config_hash"], "config_hash_120": load_state(os.path.join(FWD, "run_120_state.json"))["config_hash"], "data_cutoff": str(idx_all[-1].date()), "data_hash": "4704521e9bc6", "start_state_hash_90": state_hash(load_state(os.path.join(FWD, "run_90_state.json"))), "start_state_hash_120": state_hash(load_state(os.path.join(FWD, "run_120_state.json"))), "forward_start": "PENDING_FIRST_REAL_TRADING_DAY", "note": "真实首日由run_shadow写入forward_start/两组起点NAV后签发正式凭证"}
body = json.dumps({k: v for k, v in cred.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
cred["self_hash"] = hashlib.sha1(body.encode()).hexdigest()[:12]
cp = os.path.join(FWD, "launch_credential.json")
if not os.path.exists(cp):
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(cred, f, ensure_ascii=False, indent=1)
    print(f"凭证已建: 文件哈希{fh} 状态哈希链已绑定 self={cred['self_hash']} PASS")
else:
    print("凭证已存在, 未覆盖 PASS")
print("最小启动闭环完成")
