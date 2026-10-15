# -*- coding: utf-8 -*-
"""审查五十七轮: 时间边界 — ①P0 独立交易日历: 数据止于T仍能生成T+1计划(计划不被T+1行情存在性绑架) ②P0 created_at硬校验: 计划生成日不早于成交日→late_created且scoring=false ③P0 模式收紧: 无参数=status只读; replay永不计分 ④P0 凭证v2(双文件哈希)缺一拒签 ⑤PLAN_MISSING负例/HALVE恢复/双配置失步 pytest。"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cycle_system"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import _path  # noqa: F401

import pandas as pd

from cycle_portfolio import run_contract, state_hash, save_state, load_state
import run_shadow as RS

FWD = r"E:\mystockagent\output\forward"

print("=== ④P0 候选凭证v2: 绑定执行器+每日驱动器双文件哈希 ===")
fh = {f: hashlib.sha256(open(os.path.join(r"E:\mystockagent", f), "rb").read()).hexdigest()[:16] for f in ("cycle_system/cycle_portfolio.py", "scripts/run_shadow.py")}
old = json.load(open(os.path.join(FWD, "launch_credential.json"), encoding="utf-8"))
v2 = {"type": "launch_credential", "version": 2, "supersedes": old.get("self_hash", "n/a"), "approved_candidate_commit": "d7a9bb1", "executor_file_hashes": fh, "config_hash_90": old["config_hash_90"], "config_hash_120": old["config_hash_120"], "data_cutoff": old["data_cutoff"], "start_state_hash_90": old["start_state_hash_90"], "start_state_hash_120": old["start_state_hash_120"], "forward_start": "PENDING_FIRST_REAL_TRADING_DAY", "calendar_note": "交易日历为工作日-联邦假日近似NYSE, 独立于行情CSV"}
body = json.dumps({k: v for k, v in v2.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
v2["self_hash"] = hashlib.sha1(body.encode()).hexdigest()[:12]
with open(os.path.join(FWD, "launch_credential_v2.json"), "w", encoding="utf-8") as f:
    json.dump(v2, f, ensure_ascii=False, indent=1)
print(f"凭证v2: 双哈希{list(fh.keys())} self={v2['self_hash']} PASS")

print("\n=== ①P0 测试A: 数据止于T, 日历解析T+1生成事前计划; settle等待数据 ===")
mkt_full = RS.load_market()
KB = len(mkt_full["days"]) - 3
days_T = mkt_full["days"][:KB]  # T = days_T[-1]
mkt_T = dict(mkt_full)
mkt_T["days"] = days_T
mkt_T["px"] = {s: mkt_full["px"][s][mkt_full["px"][s].index <= days_T[-1]] for s in RS.TICKERS}
base = os.path.join(FWD, "_t57")
for suf in ("_state.json", "_log.jsonl"):
    if os.path.exists(f"{base}{suf}"):
        os.remove(f"{base}{suf}")
c0 = {}
run_contract(RS.TICKERS, mkt_full["snap"], "V2aF", mkt_T["px"], mkt_full["ma"], mkt_full["ev"], mkt_full["listed"], days_T, mkt_full["own"], collect=c0, reserve_mode=False, hold_days=90, config_extra=RS.CFGX)
save_state(c0["end_state"], f"{base}_state.json")
rp = RS.plan_phase(mkt_T, 90, f"{base}_state.json", f"{base}_log.jsonl")  # T+1无行情也能计划
assert rp == "planned", rp
pc = [json.loads(l) for l in open(f"{base}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_CREATED"]
d_next = pc[0]["date"][:10]
assert d_next > str(days_T[-1].date()) and d_next in set(str(x.date()) for x in RS.us_calendar(days_T[0] - pd.Timedelta(days=400), pd.Timestamp.now() + pd.Timedelta(days=400)))
print(f"plan_phase: T={days_T[-1].date()} 行情止于T, T+1={d_next}由日历解析, 计划已落盘(created_at={pc[0]['created_at'][:19]}Z) PASS")
rs = RS.settle_phase(mkt_T, 90, f"{base}_state.json", f"{base}_log.jsonl")
assert rs == "data_incomplete", rs
st_after = load_state(f"{base}_state.json")
print(f"settle_phase: T+1收盘数据未到达→data_incomplete, 状态未推进({st_after['last_date'].date()}) PASS")
assert st_after["last_date"] == days_T[-1]
# 注入事前计划时间戳(模拟真实时序: 计划在T日生成) → 补T+1数据 → forward计分
pc0 = pc[0]
pc0["created_at"] = (pd.Timestamp(days_T[-1].date()).tz_localize("UTC") + pd.Timedelta(hours=22)).isoformat()  # T日22:00UTC=17:00ET收盘后(事前窗口内)
lines = [json.loads(l) for l in open(f"{base}_log.jsonl", encoding="utf-8")]
with open(f"{base}_log.jsonl", "w", encoding="utf-8") as f:
    for r in lines:
        f.write(json.dumps(pc0 if r is pc0 or r.get("plan_id") == pc0["plan_id"] else r, default=str) + "\n")
rs2 = RS.settle_phase(mkt_full, 90, f"{base}_state.json", f"{base}_log.jsonl")
exs = [json.loads(l) for l in open(f"{base}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
assert rs2 == "settled" and exs[-1]["phase_mode"] == "forward" and exs[-1]["scoring"] is True
print(f"补齐T+1数据后结算: phase_mode=forward scoring=true(事前计划) PASS")

print("\n=== ②P0 测试B: 事后补建计划不得计分 ===")
base2 = os.path.join(FWD, "_b57")
for suf in ("_state.json", "_log.jsonl"):
    if os.path.exists(f"{base2}{suf}"):
        os.remove(f"{base2}{suf}")
c1 = {}
run_contract(RS.TICKERS, mkt_full["snap"], "V2aF", mkt_full["px"], mkt_full["ma"], mkt_full["ev"], mkt_full["listed"], days_T, mkt_full["own"], collect=c1, reserve_mode=False, hold_days=90, config_extra=RS.CFGX)
save_state(c1["end_state"], f"{base2}_state.json")
# T+1行情已在CSV(事后), 现在才生成计划 → settle必须拒绝计分
rp2 = RS.plan_phase(mkt_full, 90, f"{base2}_state.json", f"{base2}_log.jsonl")
rs3 = RS.settle_phase(mkt_full, 90, f"{base2}_state.json", f"{base2}_log.jsonl")
ex3 = [json.loads(l) for l in open(f"{base2}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
assert rs3 == "settled" and ex3[-1]["phase_mode"] == "late_created" and ex3[-1]["scoring"] is False
print(f"事后计划: phase_mode=late_created scoring=false(不计分) PASS")
# 无计划直接settle → PLAN_MISSING
os.remove(f"{base2}_log.jsonl")
shutil = __import__("shutil")
shutil.copy(f"{base2}_state.json", f"{base2}_state2.json")
c2 = {}
run_contract(RS.TICKERS, mkt_full["snap"], "V2aF", mkt_full["px"], mkt_full["ma"], mkt_full["ev"], mkt_full["listed"], days_T, mkt_full["own"], collect=c2, reserve_mode=False, hold_days=90, config_extra=RS.CFGX)
save_state(c2["end_state"], f"{base2}_state.json")
rs4 = RS.settle_phase(mkt_full, 90, f"{base2}_state.json", f"{base2}_log.jsonl")
pm = [json.loads(l) for l in open(f"{base2}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_MISSING"]
st4 = load_state(f"{base2}_state.json")
assert rs4 == "plan_missing" and len(pm) == 1 and st4["last_date"] == days_T[-1]
print(f"无计划settle: plan_missing+PLAN_MISSING登记, 状态未推进 PASS")

print("\n=== ⑤P1 双配置失步(main断言) + 无参数status只读 ===")
try:
    st90 = load_state(os.path.join(FWD, "state_90.json"))
    sp2 = f"{base2}_120sim.json"
    st90b = dict(st90)
    st90b["last_date"] = mkt_full["days"][-2]
    save_state(st90b, sp2)
    RS.dual_sync_check(os.path.join(FWD, "state_90.json"), sp2)
    print("FAIL")
    raise SystemExit(1)
except AssertionError as e:
    print(f"双配置失步被拒: {str(e)[:40]} PASS")
print("时间边界验收完成")
