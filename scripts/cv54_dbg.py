# -*- coding: utf-8 -*-
import json
import os
import sys

sys.path.insert(0, r"E:\mystockagent")
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent\scripts")
import _path  # noqa: F401

from cycle_portfolio import state_hash, load_state, _enc
import pandas as pd
import numpy as np

p = r"E:\mystockagent\output\forward\run_90_state.json"
st = load_state(p)
sh_now = state_hash(st)
lines = [json.loads(l) for l in open(r"E:\mystockagent\output\forward\run_90_log.jsonl", encoding="utf-8")]
ex = [r for r in lines if r["type"] == "PLAN_EXECUTED"]
cr = [r for r in lines if r["type"] == "PLAN_CREATED"]
print("EXECUTED[-1].new_state_hash:", ex[-1]["new_state_hash"])
print("state_hash(loaded final):", sh_now)
print("CREATED[0].state_hash:", _dec(cr[0]["plan"])["state_hash"] if False else json.loads(json.dumps(cr[0]["plan"]))["state_hash"])
# 逐字段: 重存再哈希
from cycle_portfolio import save_state
save_state(st, r"E:\mystockagent\output\forward\_dbg_roundtrip.json")
st2 = load_state(r"E:\mystockagent\output\forward\_dbg_roundtrip.json")
print("double roundtrip equal hash:", state_hash(st2) == sh_now, state_hash(st2))
print("num EXECUTED:", len(ex), "num CREATED:", len(cr))
# 内存对象 vs loaded: 类型差异探测
st_mem_keys = set(st.keys())
print("keys:", sorted(st_mem_keys))
print("last_date type:", type(st["last_date"]).__name__, st["last_date"])
