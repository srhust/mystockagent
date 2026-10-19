# -*- coding: utf-8 -*-
"""forward_shadow — 前向SHADOW每日CLI (独立验收第6项)。

用法:
  python cycle_system/ashare_v2/tools/forward_shadow.py plan    --day 2026-10-19 [--window-end 同日]
  python cycle_system/ashare_v2/tools/forward_shadow.py execute --day 2026-10-20   # 执行 plan 日的 T+1 成交
  python cycle_system/ashare_v2/tools/forward_shadow.py verify

口径:
  * plan: T日收盘数据 → 引擎状态+候选+评分+冻结计划(create_plan@T) → 链上归档(不可篡改);
  * execute: T+1 真实行情 → 冻结执行器成交 → 成交/拒单/NAV/持仓 链上归档;
  * run_kind: 信号日 > FREEZE.frozen_trade_date ⇒ FORWARD, 否则 REPLAY(保守, 冻结锚缺失=REPLAY);
  * 历史回放与前向归档同一链, 靠 run_kind 字段区分, 禁止混同。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                   # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                             # noqa: E402
from cycle_system.ashare_v2 import forward_ledger as FL                     # noqa: E402
from cycle_system.ashare_v2 import report as RP                             # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                   # noqa: E402
from cycle_system.ashare_v2.portfolio import PortfolioCoordinator            # noqa: E402
from cycle_system.ashare_v2.runner import Runner                            # noqa: E402
from cycle_system.ashare_v2.schemas import RunMode                          # noqa: E402

FWD_DIR = os.path.join(C.OUTPUT_DIR, "forward")
CHAIN_P = os.path.join(FWD_DIR, "forward_chain.jsonl")
STATE_P = os.path.join(FWD_DIR, "coord_state.json")
FREEZE_P = os.path.join(FWD_DIR, "FREEZE.json")
TZ = timezone(timedelta(hours=8))


def _freeze():
    return json.load(open(FREEZE_P, encoding="utf-8")) if os.path.exists(FREEZE_P) else None


def _prov():
    def git(*a):
        return subprocess.run(["git", "-C", C.REPO_ROOT] + list(a), capture_output=True,
                              text=True).stdout.strip()
    return {"code_version": git("rev-parse", "HEAD"), "config_hash": C.config_hash(),
            "wall_clock": datetime.now(TZ).isoformat()}


def cmd_plan(day: str) -> int:
    r = Runner(RunMode.SHADOW.value, portal=DataPortal(data_mode=C.PIT_RESEARCH),
               ledger_root=os.path.join(FWD_DIR, "ledger"), ledger_name="dual_cycle_v2_forward")
    r.setup()
    import pandas as pd
    if pd.Timestamp(day) not in list(r.px.index):
        print(json.dumps({"error": f"{day} 无数据(R31未更新到该日), 前向计划不可生成"}, ensure_ascii=False))
        return 2
    if os.path.exists(STATE_P):
        coord = PortfolioCoordinator.load_state(STATE_P, nav0=C.NAV0,
                                                ledger_root=os.path.join(FWD_DIR, "ledger"),
                                                ledger_name="dual_cycle_v2_forward")
        r.coord = coord
    scored = r._scored_for(pd.Timestamp(day))
    pxm, volm, _ = r._day_maps(pd.Timestamp(day))
    px_valid = {c: v for c, v in pxm.items() if v}
    ctx_vol = {c: v for c, v in volm.items() if v and pxm.get(c)}
    net = r.coord.plan_day(day, [s for s in scored if s["code"] in px_valid], px_valid,
                           ctx_vol, r.sec_of, exit_ctx_fn=lambda c, d: r._exit_ctx(c, pd.Timestamp(day)))
    rec = {"event": "PLAN", "signal_day": day, **_prov(),
           "industry_states": {ind: r.lc.industry_state(ind, day) for ind in r.lc._states},
           "candidates": [{"code": m["merged"]["code"], "engines": m["merged"]["engines"],
                           "stage": m["score"]["cycle_stage"], "pts": m["score"]["score_points"],
                           "cred": next((x.data_credibility for x in m["merged"]["records"]), ""),
                           "trust": next((x.membership_trust for x in m["merged"]["records"]), "")}
                          for m in r.opportunities if m["day"] == day],
           "sells": [{"code": s["code"], "shares": s["shares"], "engine": s["engine"],
                      "clause": s["clause"], "risk_forced": s["risk_forced"],
                      "plan_hash": s.get("plan", {}).get("plan_hash")} for s in net.sells],
           "buys": [{"code": b["code"], "budget": b["cash_budget"], "engines": b["engines"],
                     "plan_hash": b.get("plan", {}).get("plan_hash")} for b in net.buys],
           "budget_state": r.coord.plans[day]["budget_state"], "dd": r.coord.plans[day]["dd"],
           "nav_est": r.coord.nav(px_valid)}
    FL.append_record(CHAIN_P, rec, freeze=_freeze())
    r.coord.save_state(STATE_P)
    print(json.dumps({"plan": day, "sells": len(net.sells), "buys": len(net.buys),
                      "run_kind": rec.get("run_kind") or "see chain",
                      "chain_seq": FL.last_state(CHAIN_P)[0]}, ensure_ascii=False))
    return 0


def cmd_execute(day: str) -> int:
    if not os.path.exists(STATE_P):
        print(json.dumps({"error": "先执行 plan(信号日)才能 execute"}, ensure_ascii=False))
        return 2
    coord = PortfolioCoordinator.load_state(STATE_P, nav0=C.NAV0,
                                            ledger_root=os.path.join(FWD_DIR, "ledger"),
                                            ledger_name="dual_cycle_v2_forward")
    signal_day = next((d for d, r_ in coord.plans.items() if r_["exec_day"] == day), None)
    if signal_day is None:
        print(json.dumps({"error": f"无信号日的计划以{day}为执行日"}, ensure_ascii=False))
        return 2
    portal = DataPortal(data_mode=C.PIT_RESEARCH)
    panel = portal.price_panel()
    import pandas as pd
    day_ts, sig_ts = pd.Timestamp(day), pd.Timestamp(signal_day)
    px1 = panel[panel["date"] == day_ts].set_index("code")["close_adj"].to_dict()
    pxc = panel[panel["date"] == sig_ts].set_index("code")["close_adj"].to_dict()
    px1 = {c: float(v) for c, v in px1.items() if v and v == v}
    pxc = {c: float(v) for c, v in pxc.items() if v and v == v}
    if not px1:
        print(json.dumps({"error": f"{day} 无成交行情数据"}, ensure_ascii=False))
        return 2
    fills, rejects = coord.execute_day(signal_day, px1, pxc)
    rec = {"event": "EXECUTE", "signal_day": signal_day, "exec_day": day, **_prov(),
           "fills": [{k: f.get(k) for k in ("code", "action", "shares", "price", "notional",
                                            "fees", "plan_hash")} for f in fills],
           "rejects": rejects, "nav": coord.nav(px1), "cash": round(coord.ledger.cash, 4),
           "positions": {c: p["total"] for c, p in coord.ledger.pos.items()},
           "conservation_ok": abs(coord.nav(px1) - (coord.ledger.cash + sum(
               p["total"] * (px1.get(c) or coord.px_last.get(c) or 0.0)
               for c, p in coord.ledger.pos.items()))) < 1e-3}
    FL.append_record(CHAIN_P, rec, freeze=_freeze())
    coord.save_state(STATE_P)
    print(json.dumps({"execute": day, "fills": len(fills), "rejects": len(rejects),
                      "nav": rec["nav"], "chain_seq": FL.last_state(CHAIN_P)[0]}, ensure_ascii=False))
    return 0


def cmd_verify() -> int:
    v = FL.verify(CHAIN_P)
    recs = FL.read_all(CHAIN_P)
    counts = {}
    for x in recs:
        counts[x.get("run_kind", "?")] = counts.get(x.get("run_kind", "?"), 0) + 1
    print(json.dumps({"chain": v, "records": len(recs), "run_kind_counts": counts}, ensure_ascii=False))
    return 0 if v["ok"] else 1


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd, day = sys.argv[1], sys.argv[2]
    if cmd == "plan":
        return cmd_plan(day)
    if cmd == "execute":
        return cmd_execute(day)
    if cmd == "verify":
        return cmd_verify()
    print(f"未知命令: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
