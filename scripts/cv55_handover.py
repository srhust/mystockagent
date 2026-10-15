# -*- coding: utf-8 -*-
"""审查五十五轮验收: ①P0 已登记成交的修订冲突检测(crash_after_exec后价格修订→恢复被拒) ②P0 独立run_shadow入口冒烟(无新交易日退出/重复启动安全) ③P1 逐笔fills一致断言(纳入pytest场景)。"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cycle_system"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import _path  # noqa: F401

import pandas as pd

from cycle_portfolio import state_hash, save_state, load_state, plan_next_day, apply_day, run_contract, HOLD
import run_shadow as RS

FWD = r"E:\mystockagent\output\forward"

print("=== ②P0 独立run_shadow入口冒烟(bootstrap后两连跑: done→no_new) ===")
mkt = RS.load_market()
for hold in (90, 120):
    sp = os.path.join(FWD, f"state_{hold}.json")
    if not os.path.exists(sp):
        c0 = {}
        run_contract(RS.TICKERS, mkt["snap"], "V2aF", mkt["px"], mkt["ma"], mkt["ev"], mkt["listed"], mkt["days"][:-1], mkt["own"], collect=c0, reserve_mode=False, hold_days=hold, config_extra=RS.CFGX)
        save_state(c0["end_state"], sp)
r1 = RS.shadow_run(mkt, 90, os.path.join(FWD, "state_90.json"), os.path.join(FWD, "forward_log_90.jsonl"))
r2 = RS.shadow_run(mkt, 90, os.path.join(FWD, "state_90.json"), os.path.join(FWD, "forward_log_90.jsonl"))
r3 = RS.shadow_run(mkt, 120, os.path.join(FWD, "state_120.json"), os.path.join(FWD, "forward_log_120.jsonl"))
print(f"HOLD90首跑={r1} HOLD90再跑={r2} HOLD120={r3}")
assert r1 in ("done", "no_new_trading_day") and r2 == "no_new_trading_day" and r3 in ("done", "no_new_trading_day")
print("重复启动安全(第二次调用必为no_new退出) PASS")

print("\n=== ①P0 已登记成交的修订冲突检测 ===")
# 构造: crash_after_exec(执行后未存盘) → 修订T+1价格 → resume必须拒绝
base = os.path.join(FWD, "_revtest")
for suf in ("_state.json", "_log.jsonl"):
    p = f"{base}{suf}"
    if os.path.exists(p):
        os.remove(p)
KB = len(mkt["days"]) - 4
c0 = {}
run_contract(RS.TICKERS, mkt["snap"], "V2aF", mkt["px"], mkt["ma"], mkt["ev"], mkt["listed"], mkt["days"][:KB], mkt["own"], collect=c0, reserve_mode=False, hold_days=90, config_extra=RS.CFGX)
save_state(c0["end_state"], f"{base}_state.json")
st_pre = load_state(f"{base}_state.json")
d1 = mkt["days"][KB]
pp = plan_next_day(RS.TICKERS, mkt["snap"], "V2aF", mkt["px"], mkt["ma"], mkt["ev"], mkt["own"], st_pre, d1, hold_days=90, calendar=mkt["days"])
with open(f"{base}_log.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"type": "PLAN_CREATED", "plan_id": pp["plan_hash"], "date": d1.isoformat(), "plan": RS._enc(dict(pp))}, default=str) + "\n")
ca = apply_day(RS.TICKERS, mkt["snap"], "V2aF", mkt["px"], mkt["ma"], mkt["ev"], mkt["listed"], mkt["own"], st_pre, pp, d1, hold_days=90, require_config=True)
with open(f"{base}_log.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"type": "PLAN_EXECUTED", "plan_id": pp["plan_hash"], "date": d1.isoformat(), "fills": [[s, str(sig), str(fd), act, round(q, 6), round(p, 4), round(fee, 6)] for s, sig, fd, act, q, p, fee in ca["fills"]], "eq": round(float(ca["eq"].iloc[0]), 6), "new_state_hash": state_hash(ca["end_state"])}, default=str) + "\n")
# 状态未存盘(模拟崩溃) + T+1价格修订
px_rev = {s: RS.pd.Series(RS.np.where(mkt["px"][s].index == d1, mkt["px"][s].values * 1.5, mkt["px"][s].values), index=mkt["px"][s].index) for s in RS.TICKERS}
mkt_rev = dict(mkt)
mkt_rev["px"] = px_rev
try:
    RS.shadow_run(mkt_rev, 90, f"{base}_state.json", f"{base}_log.jsonl")
    print("FAIL: 修订冲突未被拒绝")
    raise SystemExit(1)
except AssertionError as e:
    print(f"修订T+1价格后恢复被拒绝: {str(e)[:48]} PASS")
# 未修订时恢复正常(同数据): 重执行→EXECUTED幂等→继续
mkt_ok = RS.load_market()
r_ok = RS.shadow_run(mkt_ok, 90, f"{base}_state.json", f"{base}_log.jsonl")
exs = [json.loads(l) for l in open(f"{base}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
assert r_ok in ("done", "no_new_trading_day") and len(exs) == 1
print(f"同数据恢复: EXECUTED仍1条(幂等), 状态={r_ok} PASS")
print("启动交接验收完成")
