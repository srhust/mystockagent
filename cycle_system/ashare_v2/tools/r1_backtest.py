# -*- coding: utf-8 -*-
"""r1_backtest — R1_RISK_RECOVERY 状态机与R0同信号流对照重放(裁定B-9/F)。

架构: R1Runner继承RawRunner(RAW记账域一致), 预测信号流(R0引擎输出)逐字节相同——
两状态机吃同一候选/退出信号, 差异只在风险编排。产出仅用于**状态迁移行为验证**:
活性(锁死后能否按预注册路径恢复)、防跳变(无直接满仓路径)、守恒(账本自洽),
不得用于声称R1提高收益(裁定红线)。

R1编排点(其余全部继承R0/RawRunner行为):
  * 每T日收盘观测(nav/dd_period=R0公式在re-anchor后的本期语义, dd_global=永不重置
    high_water记录, breadth5=adj域20日动量广度5日均线[市场独立观测量]) → step()
  * re-anchor(进PROBE/ACTIVE日): coord.peak_nav=当日nav(实例状态编排, 代码零改动;
    全局记录在r1_log永不重置——记录与决策分离, 裁定②)
  * 编排buys: COOLDOWN/LOCKOUT清空(记REJECT_R1_STATE), PROBE期cash_budget×0.25(只减,
    与P0-3单调性兼容)
  * PROBE失败而R0未冻结(期内dd>-20%区间): 向rec["sells"]注入同源risk_forced平仓
用法: python -X utf8 cycle_system/ashare_v2/tools/r1_backtest.py START END [--engines E] [--fresh]
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                               # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                    # noqa: E402
from cycle_system.ashare_v2.schemas import RunMode                           # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from raw_shadow_runner import RawRunner, super_run_tail                      # noqa: E402
from r1_state_machine import init_state, step                                # noqa: E402


class R1Runner(RawRunner):
    def __init__(self, *a, ca_events: dict | None = None, **kw):
        super().__init__(*a, **kw)
        self.ca_events = ca_events or {}        # {ex_date: [event]} —— R1_CAI 重放注入
        self.ca_rows: list = []

    def run(self) -> dict:
        self.setup()
        anchor = self._anchor_adj()
        days = self.dates
        # 预注册市场观测: breadth5(adj域, 与决策域隔离的技术观测)
        breadth = (self.px / self.px.shift(20) - 1.0 > 0).mean(axis=1).rolling(5).mean()
        r1 = init_state()
        high_water_global = 0.0
        self.r1_log = []
        for k in range(len(days) - 1):
            d, d1 = days[k], days[k + 1]
            day_s = str(pd.Timestamp(d).date())
            scored = self._scored_for(d)
            _pxm_adj, volm, _ = self._day_maps(d)
            rawc_d = {c: v for c, v in self._row(self.raw_close, d).items() if v}
            ctx_vol = {c: v for c, v in volm.items() if v and rawc_d.get(c)}
            # ---- R1观测与迁移(plan前) ----------------------------------------
            nav_t = self.coord.nav(rawc_d)
            high_water_global = max(high_water_global, nav_t)
            dd_global = nav_t / high_water_global - 1.0
            br = float(breadth.loc[d]) if d in breadth.index and pd.notna(breadth.loc[d]) else None
            liq_done = not {c: p for c, p in self.coord.ledger.pos.items() if p["total"]}
            obs = {"day": day_s, "dd_period": nav_t / max(self.coord.peak_nav, 1e-9) - 1.0,
                   "dd_global": dd_global, "breadth5": br, "nav": nav_t,
                   "pos_exposure": 1.0 - self.coord.ledger.cash / max(nav_t, 1e-9),
                   "lockout_liquidated": liq_done}
            prev_state = r1["state"]
            r1, action, log = step(r1, obs)
            log["r1_state_after"] = r1["state"]
            # re-anchor: 本期决策锚在PROBE起点与ACTIVE恢复点重设(全局记录不重置)
            if r1["state"] != prev_state and r1["state"] in ("PROBE_REENTRY", "ACTIVE"):
                self.coord.peak_nav = nav_t
                obs["dd_period"] = 0.0
                log["reanchor_peak_nav"] = round(nav_t, 2)
            self.r1_log.append({**log, **{k_: obs[k_] for k_ in ("dd_global", "breadth5", "nav")}})
            # ---- R0信号/计划(引擎行为不变) -----------------------------------
            net = self.coord.plan_day(day_s, [s for s in scored if s["code"] in rawc_d],
                                      rawc_d, ctx_vol, self.sec_of,
                                      exit_ctx_fn=lambda c, dd, _d=d: self._exit_ctx(c, _d))
            rec = self.coord.plans[day_s]
            # ---- R1编排buys --------------------------------------------------
            for b in list(rec["buys"]):
                if not action["allow_new"]:
                    rec["buys"].remove(b)
                    self.trade_audit.append({"signal_day": day_s, "exec_day": rec["exec_day"],
                                             "code": b["code"], "action": "REJECT_R1_STATE",
                                             "plan_hash": b.get("plan", {}).get("plan_hash", ""),
                                             "shares": 0, "exec_raw_open": None, "val_raw_close": None,
                                             "val_adj_close": None, "prev_raw_close": rawc_d.get(b["code"]),
                                             "fees": None, "cash_delta": 0.0,
                                             "origin": f"r1={r1['state']}"})
                elif action["budget_scale"] < 1.0:
                    b["cash_budget"] = round(b["cash_budget"] * action["budget_scale"], 2)
                    b["_r1_probe_scale"] = action["budget_scale"]
            # PROBE失败而R0未自动强平(期内dd>-20%区间): 注入同源risk_forced平仓
            if action["force_liquidate"] and rec["budget_state"] != "EXCEEDED":
                from cycle_system import ashare_executor as ex               # 冻结执行器, 只读复用
                have = {(s["engine"], s["code"]) for s in rec["sells"]}
                for v in sorted(self.coord.virtuals, key=lambda x: x.risk_budget_used):
                    if (v.engine, v.code) in have or not v.shares:
                        continue
                    ref = rawc_d.get(v.code) or self.coord.px_last.get(v.code)
                    if ref is None:
                        continue
                    s_ = {"code": v.code, "shares": int(v.shares), "engine": v.engine,
                          "clause": "R1 PROBE/LOCKOUT强制平仓(预注册)", "risk_forced": True,
                          "pos_id": v.position_id, "score": v.risk_budget_used, "kind": "FORCED"}
                    # 执行器计划必须与plan_day同源冻结(execute_day要求s["plan"], 缺=stale拒)
                    s_["plan"] = ex.create_plan(v.code, "SELL", day_s, ref, shares=s_["shares"],
                                                created_at=f"{day_s}T15:05:00+08:00")
                    self.coord.ledger.register_plan(s_["plan"])
                    rec["sells"].append(s_)
            # ---- 执行(T+1 raw open, 与R0对照域一致) ---------------------------
            rawo_1 = {c: v for c, v in self._row(self.raw_open, d1).items() if v}
            rawc_1 = {c: v for c, v in self._row(self.raw_close, d1).items() if v}
            fills, rejects = self.coord.execute_day(day_s, rawo_1, rawc_d)
            # ---- R1_CAI 企业行动入账(CAI.0-PREREG; ca_events=None 时零行为) ----
            if getattr(self, "ca_events", None):
                from ca_apply import apply_dividend, tax_tier_after
                _d1s = rec["exec_day"]
                for e_ in self.ca_events.get(_d1s, []):
                    if e_["type"] != "DIVIDEND":
                        # CAI.BOUNDARY-2 防御行: 送转未启用配置下若出现持仓命中, 强制告警入审计
                        _bh = sum(int(v.shares) for v in self.coord.virtuals
                                  if v.code == e_["code"] and v.shares > 0)
                        if _bh > 0:
                            self.ca_rows.append({
                                "day": _d1s, "code": e_["code"], "type": "BONUS_UNAPPLIED_ALERT",
                                "shares_basis": _bh, "amount": 0.0, "tax_chosen": "N/A",
                                "days_held": "", "source_event":
                                    f"held={_bh}>0而送转入账未启用(CAI.BOUNDARY-2): 该窗禁称完整账本"})
                        continue          # BONUS重放内调整=hits 0(预注册); 一致性由事件加载旁证核
                    _lots = [{"shares": int(v.shares), "entry_day": v.opened_day}
                             for v in self.coord.virtuals
                             if v.code == e_["code"] and v.shares > 0]
                    self.coord.ledger.cash, _rows = apply_dividend(
                        float(self.coord.ledger.cash), _lots, e_, None)
                    self.ca_rows.extend(_rows)
            for f in fills:
                if f.get("status") != "FILLED":
                    continue
                self.trade_audit.append({
                    "signal_day": day_s, "exec_day": rec["exec_day"], "code": f["code"],
                    "action": f["action"], "plan_hash": f.get("plan_hash", ""),
                    "shares": f["shares"], "exec_raw_open": f["price"],
                    "val_raw_close": rawc_1.get(f["code"]),
                    "val_adj_close": (self._row(self.px, d1).get(f["code"])),
                    "prev_raw_close": rawc_d.get(f["code"]), "fees": f.get("fees"),
                    "cash_delta": f.get("cash_delta"),
                    "origin": "R1_RAW_OPEN", "r1_state": r1_log_state(r1, prev_state)})
            nav_raw = self.coord.nav(rawc_1)
            pos = {c: p["total"] for c, p in self.coord.ledger.pos.items()}
            adj_row = self._row(self.px, d1)
            nav_adj = self.coord.ledger.cash + sum(
                sh * ((adj_row.get(c) or 0.0) / anchor[c]) for c, sh in pos.items() if c in anchor)
            self.nav_hist.append({"day": rec["exec_day"], "nav": nav_raw,
                                  "cash": round(self.coord.ledger.cash, 4), "positions": pos,
                                  "nav_adj_track": round(nav_adj, 4),
                                  "budget_state": rec["budget_state"],
                                  "r1_state": r1["state"], "dd_global": round(dd_global, 4)})
            self.plan_log.append({"signal_day": day_s, "exec_day": rec["exec_day"],
                                  "n_scored": len(scored), "n_buys": len(rec["buys"]),
                                  "n_sells": len(rec["sells"]), "budget_state": rec["budget_state"],
                                  "r1_state": r1["state"], "dd": rec["dd"], "dd_global": round(dd_global, 4),
                                  "n_rejects_alloc": len(rec["alloc"]["rejects"]),
                                  "engine_funding": rec["alloc"]["engine_funding"],
                                  "cash_conservation": rec["alloc"]["cash_conservation"],
                                  "n_exec_rejects": len(rejects),
                                  "n_fills": len(fills)})
        res = super_run_tail(self, days)
        res["r1_log"] = self.r1_log
        return res


def r1_log_state(r1, prev):
    return r1["state"] if r1["state"] == prev else f"{prev}->{r1['state']}"


def summarize(res: dict, outdir: str, tag: str) -> dict:
    """状态迁移时间线 + R0对照(仅行为字段) + 活性检验。"""
    log = res["r1_log"]
    trans = [l for l in log if "->" in str(l.get("rule", "")) or l["from_state"] != l.get("to_state")]
    nav = pd.DataFrame(res["nav_hist"])
    by_state = {}
    for l in log:
        by_state[l["from_state"]] = by_state.get(l["from_state"], 0) + 1
    # R0对照: 读既有raw_bt MERGED产物(同窗同信号)
    r0_dir = os.path.join(C.OUTPUT_DIR, "raw_bt",
                          tag.replace("ca_r1_", "").replace("r1_", ""))
    r0_cmp = {}
    if os.path.exists(os.path.join(r0_dir, "nav_hist_full.csv")):
        n0 = pd.read_csv(os.path.join(r0_dir, "nav_hist_full.csv"), encoding="utf-8-sig")
        r0_cmp = {"r0_final_nav": round(float(n0['nav'].iloc[-1]), 2),
                  "r0_frozen_from": next((str(r["day"]) for _, r in n0.iterrows()
                                          if r.get("budget_state") == "EXCEEDED"), None),
                  "r0_days_locked": int((n0["budget_state"] == "EXCEEDED").sum()) if "budget_state" in n0 else None}
    out = {"version": "R1.0-PREREG", "tag": tag,
           "disclaimer": "STATE-MACHINE-BEHAVIOR-VERIFICATION ONLY: 状态迁移活性/防跳变/守恒验证; "
                         "非收益改善宣称; REPLAY+RESEARCH+企业行动未入账(CORPORATE_ACTION_UNVERIFIED)",
           "state_day_counts": by_state,
           "transition_timeline": [{k: l.get(k) for k in
                                    ("day", "from_state", "to_state", "rule", "dd_period",
                                     "dd_global", "breadth5", "reanchor_peak_nav")} for l in trans],
           "n_transitions": len(trans),
           "activity_check": {"ever_recovered_after_first_lock": any(
               l["from_state"] == "LOCKOUT" and l["to_state"] in ("COOLDOWN",) for l in trans),
               "reached_active_again": any(l.get("to_state") == "ACTIVE" for l in trans),
               "days_in_terminal_flatline": int((nav["r1_state"] == "LOCKOUT").sum()) if len(nav) else 0},
           "final_nav_r1": round(float(nav["nav"].iloc[-1]), 2) if len(nav) else None,
           "r0_comparison_behavior_only": r0_cmp,
           "fills": len(res["trade_audit"]),
           "mode": res.get("mode")}
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    engines = None
    if "--engines" in sys.argv:
        engines = sys.argv[sys.argv.index("--engines") + 1]
    start, end = args[0], args[1]
    CA_MODE = "--ca" in sys.argv                      # R1_CAI: 企业行动入账重放(默认关=历史行为逐数不变)
    ca_events: dict = {}
    ca_audit: dict = {"mode": "OFF"}
    if CA_MODE:
        import glob as _glob
        from ca_apply import apply_dividend           # noqa: F401  循环内消费, 此处仅声明依赖
        pool = os.path.join(C.DATA_DIR, "ashare_history_2000", "corporate_actions",
                            "full_replay_pool")
        n_b = 0
        for f in _glob.glob(os.path.join(pool, "*.events.json")):
            j = json.load(open(f, encoding="utf-8"))
            if j["_meta"]["status"] != "COVERED_EVENTS":
                continue                              # CAI.6: 只消费已验证源
            for e_ in j["events"]:
                if start <= e_["ex_date"] <= end:
                    ca_events.setdefault(e_["ex_date"], []).append(e_)
                    if e_["type"] != "DIVIDEND":
                        n_b += 1
        ca_audit = {"mode": "R1_CAI_ON", "events_in_window": sum(len(v) for v in ca_events.values()),
                    "days_with_events": len(ca_events), "non_dividend_events": n_b,
                    "note": "入账=DIVIDEND应计制(ex_date当日盘后, CAI.1); BONUS重放内股数调整未启用"
                            "(持仓命中0, CAI.4旁证核在coverage侧); 税档按lot持有期(CAI.3)"}
    tag = ("ca_r1_" if CA_MODE else "r1_") + f"{start}_{end}" + (f"_{engines}" if engines else "")
    outdir = os.path.join(C.OUTPUT_DIR, ("ca_bt" if CA_MODE else "r1_bt"), tag)
    import shutil
    shutil.rmtree(outdir, ignore_errors=True)          # 账本追加污染纪律: 每次全新目录
    os.makedirs(outdir, exist_ok=True)
    runner = R1Runner(RunMode.SHADOW.value, portal=DataPortal(data_mode=C.PIT_RESEARCH),
                      window=(start, end), allow_engines=engines,
                      ledger_root=os.path.join(outdir, "ledger"),
                      ledger_name=f"r1_{tag.replace('-', '').replace(':', '')}",
                      ca_events=ca_events)
    res = runner.run()
    if CA_MODE:
        pd.DataFrame(runner.ca_rows).to_csv(os.path.join(outdir, "ca_events.csv"),
                                            index=False, encoding="utf-8-sig")
        ca_audit["dividend_amount_total"] = round(sum(float(r["amount"]) for r in runner.ca_rows
                                                      if r["tax_chosen"] != "NO_HOLDER"), 2)
        ca_audit["n_audit_rows"] = len(runner.ca_rows)
        ca_audit["tier_dist"] = {}
        for r in runner.ca_rows:
            ca_audit["tier_dist"][r["tax_chosen"]] = ca_audit["tier_dist"].get(r["tax_chosen"], 0) + 1
        json.dump(ca_audit, open(os.path.join(outdir, "ca_audit.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    summ = summarize(res, outdir, tag)
    if CA_MODE:
        summ["ca_integration"] = ca_audit
        summ["version"] = "R1_CAI.0-PREREG(R1状态机+企业行动入账, 独立版本非R1行为延续)"
    from cycle_system.ashare_v2 import report as RP                          # 账本五件套(selfcheck验收前置)
    files = RP.write_all(outdir, runner, res)
    pd.DataFrame([{k: v for k, v in h.items() if k != "positions"} for h in res["nav_hist"]]
                 ).to_csv(os.path.join(outdir, "nav_hist_full.csv"),
                          index=False, encoding="utf-8-sig")        # positions展开防重复行纪律同RawRunner
    pd.DataFrame(res["trade_audit"]).to_csv(os.path.join(outdir, "trade_audit.csv"),
                                            index=False, encoding="utf-8-sig")
    pd.DataFrame(res["r1_log"]).to_csv(os.path.join(outdir, "r1_state_log.csv"),
                                       index=False, encoding="utf-8-sig")
    json.dump(summ, open(os.path.join(outdir, "r1_summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print(json.dumps(summ, ensure_ascii=False, indent=1, default=str)[:3000])
