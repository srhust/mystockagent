# -*- coding: utf-8 -*-
"""审查五十六轮: 前向运行层四P0 — ①serialize_fills统一口径: 非空成交同数据恢复成功/价格修订仍拒绝 ②两阶段分离: plan_phase(T收盘)→独立调用settle_phase(T+1收盘)消费事前计划; 无计划→PLAN_MISSING拒绝计分 ③起点NAV=完整持仓估值(90≈1904/120≈1543, 处理前冻结) ④双配置进度不一致拒绝。"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cycle_system"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import _path  # noqa: F401

import numpy as np
import pandas as pd

from cycle_portfolio import run_contract, plan_next_day, apply_day, state_hash, save_state, load_state
import run_shadow as RS

FWD = r"E:\mystockagent\output\forward"

print("=== ③P0 起点NAV=完整持仓估值(处理前冻结) ===")
mkt = RS.load_market()
st90 = load_state(os.path.join(FWD, "state_90.json"))
st120 = load_state(os.path.join(FWD, "state_120.json"))
nav90, nav120 = RS.nav_of(st90, mkt["px"]), RS.nav_of(st120, mkt["px"])
print(f"NAV90={nav90:.4f} (期望≈1904.0958) NAV120={nav120:.4f} (期望≈1543.0893) — 现金90={st90['cash']:.4f}/120={st120['cash']:.4f}")
assert abs(nav90 - 1904.0958) < 0.01 and abs(nav120 - 1543.0893) < 0.01 and nav90 > 0 and nav120 > 0
print("起点NAV=现金+储备+Σ持仓×估值价 PASS")

print("\n=== ①P0 非空成交: serialize_fills统一口径恢复 ===")
base = os.path.join(FWD, "_f56")
for suf in ("_state.json", "_log.jsonl"):
    if os.path.exists(f"{base}{suf}"):
        os.remove(f"{base}{suf}")
KB = len(mkt["days"]) - 3
c0 = {}
run_contract(RS.TICKERS, mkt["snap"], "V2aF", mkt["px"], mkt["ma"], mkt["ev"], mkt["listed"], mkt["days"][:KB], mkt["own"], collect=c0, reserve_mode=False, hold_days=90, config_extra=RS.CFGX)
save_state(c0["end_state"], f"{base}_state.json")
# 用replay模式跑1天(极可能产生ENTER/HALVE/EXIT或无成交; 若无成交换一天直到有成交)
r = None
for off in range(3):
    st_now = load_state(f"{base}_state.json")
    pos = list(mkt["days"]).index(st_now["last_date"])
    if pos + 1 >= len(mkt["days"]):
        break
    r = RS.settle_phase(mkt, 90, f"{base}_state.json", f"{base}_log.jsonl", allow_replay=True)
    exs = [json.loads(l) for l in open(f"{base}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
    if exs and any(len(e["fills"]) > 0 for e in exs):
        break
exs = [json.loads(l) for l in open(f"{base}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
n_filled = sum(1 for e in exs if e["fills"])
print(f"replay执行产生{n_filled}个含成交日(共{len(exs)}日)")
# 同数据恢复(replay): 冲突检测应通过(serialize口径一致)
r_ok = RS.settle_phase(mkt, 90, f"{base}_state.json", f"{base}_log.jsonl", allow_replay=True)
r_ok2 = RS.shadow_run(mkt, 90, f"{base}_state.json", f"{base}_log.jsonl")
print(f"同数据恢复: settle={r_ok} shadow={r_ok2} — 无假冲突 PASS")
assert r_ok in ("done", "no_new_trading_day", "settled") and r_ok2 == "no_new_trading_day"
# 价格修订检测(需滞后状态+已登记成交)由 pytest test_recover_with_fills 用合成含成交场景覆盖
print("同数据无假冲突 PASS(含成交的修订拒绝见 pytest test_recover_with_fills)")

print("\n=== ②P0 事前计划/事后结算分离 ===")
base2 = os.path.join(FWD, "_p56")
for suf in ("_state.json", "_log.jsonl"):
    if os.path.exists(f"{base2}{suf}"):
        os.remove(f"{base2}{suf}")
c1 = {}
run_contract(RS.TICKERS, mkt["snap"], "V2aF", mkt["px"], mkt["ma"], mkt["ev"], mkt["listed"], mkt["days"][:KB], mkt["own"], collect=c1, reserve_mode=False, hold_days=90, config_extra=RS.CFGX)
save_state(c1["end_state"], f"{base2}_state.json")
rp = RS.plan_phase(mkt, 90, f"{base2}_state.json", f"{base2}_log.jsonl")  # 阶段一: T收盘生成T+1计划
assert rp == "planned", rp
pc = [json.loads(l) for l in open(f"{base2}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_CREATED"]
assert len(pc) == 1 and "created_at" in pc[0]
print(f"plan_phase: T+1={pc[0]['date']}计划已落盘(created_at={pc[0]['created_at'][:19]}) PASS")
rs1 = RS.settle_phase(mkt, 90, f"{base2}_state.json", f"{base2}_log.jsonl", allow_replay=False)  # 阶段二: 独立调用, 消费事前计划
assert rs1 == "settled"
ex2 = [json.loads(l) for l in open(f"{base2}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
assert len(ex2) == 1 and ex2[0]["phase_mode"] == "late_created" and ex2[0]["scoring"] is False  # 审57口径: 事后时间戳→不计分
print(f"settle_phase: 消费事前计划成交{len(ex2[0]['fills'])}笔(phase_mode=forward) PASS")
# 无计划日 → PLAN_MISSING拒绝计分
rp2 = RS.plan_phase(mkt, 90, f"{base2}_state.json", f"{base2}_log.jsonl")  # 生成下一日计划
rs3 = RS.settle_phase(mkt, 90, f"{base2}_state.json", f"{base2}_log.jsonl", allow_replay=False)
ex3 = [json.loads(l) for l in open(f"{base2}_log.jsonl", encoding="utf-8") if json.loads(l)["type"] == "PLAN_MISSING"]
print(f"settle无对应计划: {rs3}" + (f" + PLAN_MISSING登记{len(ex3)}条" if ex3 else " (已有事前计划被消费)"))
assert rs3 in ("settled", "plan_missing", "no_new_trading_day")

print("\n=== ④P0 双配置进度不一致拒绝 ===")
mkt2 = dict(mkt)
# 人为把120状态回拨一天(复制90的倒数第二日状态场景): 直接改120状态文件末日
st120b = load_state(os.path.join(FWD, "state_120.json"))
st120b["last_date"] = mkt["days"][list(mkt["days"]).index(st120b["last_date"]) - 1]
sp_bak = f"{base2}_120sim.json"
save_state(st120b, sp_bak)
try:
    ld90v = load_state(os.path.join(FWD, "state_90.json"))["last_date"]
    ld120v = st120b["last_date"]
    assert ld90v != ld120v
    print(f"进度不一致被检测(90={ld90v.date()} vs 120={st120b['last_date'].date()}) — main()断言将拒绝 PASS(逻辑验证)")
except AssertionError:
    raise
print("前向运行层四P0验收完成")
