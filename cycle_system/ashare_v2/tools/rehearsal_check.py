# -*- coding: utf-8 -*-
"""rehearsal_check — live execute后对rehearsal域的一次性对账(验收: T+1成交必须进state)。

输入: forward/coord_state_rehearsal.json + forward/ledger/ledger_dual_cycle_v2_fwd_rehearsal.jsonl
核对: H1 cash==NAV0+Σcash_delta · H2 持仓股数==账本净额 · H3 ledger_trading_day==末成交日
     · H4 账本plan_hash⊆链上PLAN记录 · H5 正式state无FORWARD成交写痕+正式账本不存在
输出: independent/rehearsal_check.json。全部只读。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                              # noqa: E402
from cycle_system.ashare_v2 import forward_ledger as FL                     # noqa: E402
from cycle_system.ashare_v2.tools import forward_shadow as FS               # noqa: E402

out: dict = {}
rep = FS._state_rehearsal_p()
led = os.path.join(FS.FWD_DIR, "ledger", f"ledger_{FS.LEDGER_NAME_REHEARSAL}.jsonl")
out["H0_inputs"] = {"state_rehearsal": os.path.exists(rep), "ledger": os.path.exists(led)}
if out["H0_inputs"]["state_rehearsal"] and out["H0_inputs"]["ledger"]:
    st = json.load(open(rep, encoding="utf-8"))
    fills = [json.loads(l) for l in open(led, encoding="utf-8")]
    cash = C.NAV0 + sum(float(r["cash_delta"]) for r in fills)
    out["H1_cash"] = {"state_cash": st.get("ledger_cash"), "recomputed": round(cash, 4)}
    out["H1_cash"]["ok"] = st.get("ledger_cash") is not None and abs(float(st["ledger_cash"]) - cash) < 0.01
    net: dict = {}
    for r in fills:
        net[r["code"]] = net.get(r["code"], 0) + (int(r["shares"]) if r["action"] == "BUY" else -int(r["shares"]))
    pos = {str(k): int(v) for k, v in (st.get("positions") or {}).items() if int(v)}
    net = {k: v for k, v in net.items() if v}
    out["H2_positions"] = {"state": pos, "ledger_net": net}
    out["H2_positions"]["ok"] = pos == net
    last_exec = max(str(r["execution_date"]) for r in fills) if fills else None
    out["H3_trading_day"] = {"state": st.get("ledger_trading_day"), "last_fill": last_exec}
    out["H3_trading_day"]["ok"] = st.get("ledger_trading_day") == last_exec
    chain = FL.read_all(FS.CHAIN_P)
    phs = {r.get("plan", {}).get("plan_hash") for r in chain if r.get("event") == "PLAN"}
    phs |= {h for r in chain if r.get("event") == "PLAN" for h in (r.get("child_plan_hashes") or [])}
    bad = [r["plan_hash"] for r in fills if r.get("plan_hash") and r["plan_hash"] not in phs]
    out["H4_plan_hash_provenance"] = {"fills": len(fills), "orphans": bad[:5]}
    out["H4_plan_hash_provenance"]["ok"] = not bad
    formal_led = os.path.join(FS.FWD_DIR, "ledger", f"ledger_{FS.LEDGER_NAME}.jsonl")
    out["H5_formal_zero_write"] = {"formal_ledger_exists": os.path.exists(formal_led),
                                   "chain_formal_gate": sum(1 for r in chain if r.get("formal_gate") == "FORMAL")}
    out["H5_formal_zero_write"]["ok"] = (not os.path.exists(formal_led)) and out["H5_formal_zero_write"]["chain_formal_gate"] == 0
    out["all_ok"] = all(v.get("ok", True) for k, v in out.items() if k.startswith("H"))
op = os.path.join(C.OUTPUT_DIR, "independent", "rehearsal_check.json")
json.dump(out, open(op, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out.get("all_ok") else 1)
