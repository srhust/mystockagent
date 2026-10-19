# -*- coding: utf-8 -*-
"""portfolio — PortfolioCoordinator: 虚拟持仓 + 真实账本唯一协调器 (阶段E实装, §6)。

结构保证(测试锁):
  * **卖出先于买入**: 同一执行日内 sells 全部入账后 buys 才允许(真实卖款养真实买, 不估空);
  * **T+1**: 全部成交走冻结 ashare_executor(create_plan@T收盘 → execute_plan@T+1;
    Ledger.advance_day 控制可卖, 当日买入不可卖——复用冻结实现, 不自造);
  * **同股双引擎**: 资金只承诺一次(一条BUY计划), 虚拟头寸按引擎比例拆分(整手对齐+余数归
    主引擎), 归因两条独立; 引擎退出只卖自引擎份额, 拆分破坏整手时显式延迟(不静默拆);
  * **风险预算优先**: 回撤峰值口径 NAV 跌破预算 → budget_state=EXCEEDED(冻结新增)且
    低分持仓进入强制卖出队列(risk_forced 覆盖换仓);
  * 缺价估值: 成交价缺失 → 显式 last-known 价 + annotation(旧Ledger.nav缺价"last"未实现,
    本层先补全价字典再入账, 不静默忽略);
  * 账本不可触碰冻结语义: 只调用 create_plan/execute_plan/Ledger, 不修改执行器与 swing_m1。
"""
from __future__ import annotations

import os
from copy import deepcopy

from cycle_system.ashare_v2 import _path  # noqa: F401,E402  旧模块路径引导(必须先于ashare_executor)
import ashare_executor as ex  # noqa: E402  冻结执行器: 只读复用

from cycle_system.ashare_v2 import capital_allocator as K
from cycle_system.ashare_v2 import config as C
from cycle_system.ashare_v2.exit_manager import REDUCE_FRACTION, ExitManager
from cycle_system.ashare_v2.schemas import (Engine, ExitClass, NetTarget, VirtualPosition)


class PortfolioCoordinator:
    def __init__(self, nav0: float = C.NAV0, ledger_root: str | None = None,
                 exit_manager: ExitManager | None = None, ledger_name: str = "dual_cycle_v2"):
        self.nav0 = nav0
        root = ledger_root or os.path.join(C.OUTPUT_DIR, "ledger")
        self.ledger = ex.Ledger(ledger_name, nav0=nav0, root=root)
        self.em = exit_manager or ExitManager()
        self.virtuals: list = []            # VirtualPosition(两引擎独立归因)
        self.peak_nav = nav0
        self.plans: dict = {}               # day → {"sells":[...], "buys":[...]}
        self.execution_log: list = []
        self.rejects: list = []
        self.annotations: list = []
        self.px_last: dict = {}             # code → 最近已知收盘价(缺价last口径)
        self.pos_meta: dict = {}            # position_id → {"opened_px":...} 归因实现盈亏需要
        self.realized_log: list = []        # 每笔卖出实现损益(引擎归因口径, 非收益宣称)
        self._seq = 0

    # ── 辅助 ──
    def _next_trading_day(self, day: str) -> str:
        import pandas as pd
        return str(ex._next_bdate(pd.Timestamp(day)).date())

    def _real_value(self, px: dict) -> float:
        tot = 0.0
        for c, p in self.ledger.pos.items():
            price = px.get(c) or self.px_last.get(c)
            if price is None:
                self.annotations.append(f"NAV缺价且无last-known: {c}(显式, 非静默)")
                continue
            self.px_last.setdefault(c, price)
            tot += p["total"] * price
        return tot

    def nav(self, px: dict) -> float:
        return round(self.ledger.cash + self._real_value(px), 4)

    def _split_lots(self, filled: int, weights: list, code: str = "000000") -> list:
        """整手对齐拆分: 每引擎向下取整手, 余数归权重最大引擎。"""
        base, step = ex.lot_step(code)
        outs = [filled * w / (sum(weights) or 1.0) for w in weights]
        lots = [int(o // step) * step for o in outs]
        rem = filled - sum(lots)
        lots[max(range(len(outs)), key=lambda i: outs[i])] += rem
        if len(lots) > 1 and 0 in lots:                # 缺陷修复: 拆分零股归首引擎不再静默
            self.annotations.append(f"双引擎拆分零股归并主引擎: {code} filled={filled} lots={lots}(整手不可分, 归因偏置显式)")
        return lots

    # ── 计划(信号日T) ──
    def _plan_budget(self, code: str, val: float, px_ref: float, avail_cash: float) -> float | None:
        """预算=名义值+执行器同源余量(raw=budget*0.999/px 反推≥1整手+费), 且≤真实可用资金。"""
        base, step = ex.lot_step(code)
        budget = round(val + val * 0.0012 + 6.0, 2)
        budget = min(budget, round(avail_cash, 2))
        need = base * px_ref / 0.999 + 6.0          # 一手最小可行预算(含费)
        if px_ref <= 0 or budget < need:
            return None
        return budget

    def plan_day(self, signal_day: str, scored: list, px: dict, vol20: dict, ind_of: dict,
                 exit_ctx_fn=None, cash_extra_note: str = "") -> NetTarget:
        exit_ctx_fn = exit_ctx_fn or (lambda code, day: {})
        for c, v in px.items():
            if v:
                self.px_last[c] = v
        nav_est = self.nav(px)
        self.peak_nav = max(self.peak_nav, nav_est)
        dd = nav_est / self.peak_nav - 1.0
        budget_state = "EXCEEDED" if dd <= -C.RISK_BUDGET_DRAWDOWN else "NORMAL"

        sells = []
        # 1) 引擎退出/减仓
        for v in list(self.virtuals):
            ctx = dict(exit_ctx_fn(v.code, signal_day))
            cls_list = self.em.evaluate(v, ctx)
            if not cls_list:
                continue
            worst = max(cls_list, key=lambda t: {"WARNING": 0, "REDUCE": 1, "EXIT": 2}[t[0]])
            cls, clause, frac = worst
            if cls == ExitClass.WARNING.value:
                continue
            total = sum(vv.shares for vv in self.virtuals if vv.code == v.code)
            want = v.shares if cls == ExitClass.EXIT.value else ex.round_lot(v.code, int(v.shares * frac))
            if want <= 0:
                if cls == ExitClass.REDUCE.value:      # 缺陷修复: <200股头寸REDUCE整手取零此前静默消失
                    self.annotations.append(f"REDUCE不足一手延迟: {v.code} shares={v.shares}×{frac}(§6整手显式)")
                continue
            if want < total and want < ex.lot_step(v.code)[0]:
                self.annotations.append(f"REDUCE零股不可拆卖延迟: {v.code} want={want}(§6整手显式)")
                continue
            if want == v.shares and cls != ExitClass.EXIT.value:
                want = ex.round_lot(v.code, want)
                if want <= 0:
                    continue
            if want != v.shares and cls == ExitClass.EXIT.value:
                want = v.shares
            sells.append({"code": v.code, "shares": int(want), "engine": v.engine, "clause": clause,
                          "risk_forced": False, "pos_id": v.position_id, "score": v.risk_budget_used,
                          "kind": "EXIT" if cls == ExitClass.EXIT.value else "REDUCE"})
        # 2) 预算超限: 强制卖最低分(覆盖换仓优先, §5.4), 去重同(引擎,代码)
        if budget_state == "EXCEEDED":
            have = {(s["engine"], s["code"]) for s in sells}
            for v in sorted(self.virtuals, key=lambda v: v.risk_budget_used):
                if (v.engine, v.code) in have:
                    continue
                sells.append({"code": v.code, "shares": int(v.shares), "engine": v.engine,
                              "clause": "风险预算强制平仓(回撤预算)", "risk_forced": True,
                              "pos_id": v.position_id, "score": v.risk_budget_used})
                break  # 预算态冻结新增, 首批一笔(逐日推进)

        # 3) 分配(预算EXCEEDED→allocator自动冻结 buys)
        holdings = []
        by_code = {}
        for v in self.virtuals:
            by_code.setdefault(v.code, []).append(v)
        for code, vs in by_code.items():
            p = self.ledger.pos.get(code, {"total": 0})
            value = p["total"] * (px.get(code) or self.px_last.get(code) or 0.0)
            holdings.append({"code": code, "value": value,
                             "score_points": max(v.risk_budget_used for v in vs),
                             "industry": ind_of.get(code, ""), "vol20": vol20.get(code, 0.0),
                             "risk_forced": any(s["code"] == code and s["risk_forced"] for s in sells)})
        # 计划卖出的预估回款计入 allocator 可用现金(执行序先卖后买保证真实到账)。
        # 缺陷修复(审查B/BUG-3): 回款用与执行器同源的 ex.fees()(含¥5佣金floor), 不再 flat 0.11% 高估
        est_proceeds = 0.0
        for s in sells:
            ref_s = px.get(s["code"]) or self.px_last.get(s["code"]) or 0.0
            est_proceeds += max(0.0, s["shares"] * ref_s - ex.fees(s["code"], s["shares"] * ref_s, True))
        avail_pool = self.ledger.cash + est_proceeds
        plan = K.allocate(deepcopy(scored), holdings, cash=avail_pool,
                          nav=max(nav_est, 1e-9),
                          ctx={"px": px, "vol20": vol20, "industry": ind_of, "budget_state": budget_state})
        buys = []
        for b in plan["buys"]:
            code = b["code"]
            wts = [1.0] if len(b["engines"]) <= 1 else [1.0] * len(b["engines"])
            budget_ = self._plan_budget(code, b["value"], b["px"], avail_pool)
            if budget_ is None:
                self.annotations.append(f"买入预算不足一手放弃: {code}(显式)")
                plan["rejects"].append({"code": code, "reason": "E_BUDGET_UNFEASIBLE",
                                        "score_points": b["score_points"]})   # BUG-6修复: E层撤销必落拒单表
                continue
            buys.append({"code": code, "cash_budget": budget_, "engines": b["engines"],
                         "score_points": b["score_points"], "industry": b["industry"],
                         "record_ids": b["record_ids"], "plan_weights": wts})
        # 3.5) 换仓执行化(独立验收第4项): allocator swap记录 → 真实卖(out)全部+买(in)等值换入。
        #      触发/edge/成本判据全用allocator已冻结规则; 本层只做转换+一致性校验(三上限同源复验)。
        swap_applied = 0
        # BUG-1修复(审查B): 组合σ复验必须含当日已排buys, 否则swap叠穿PORT_DAILY_RISK_FRAC
        risk_now = sum(h["value"] * (h.get("vol20") or 0.0) for h in holdings) \
            + sum(b["cash_budget"] * (vol20.get(b["code"]) or 0.0) for b in buys)
        if any((h.get("vol20") or 0.0) <= 0 for h in holdings):
            self.annotations.append("持仓缺vol20按σ=0计(显式标注, 组合风险或被低估): "
                                    + ",".join(h["code"] for h in holdings if (h.get("vol20") or 0) <= 0))
        swap_pool = 0.0                            # BUG-2修复: 换仓卖款逐笔加入可用池(卖先于买语义)
        ind_after = {k: round(w * nav_est, 2) for k, w in plan["industry_weights_after"].items()}
        in_seen = {b["code"] for b in buys}

        def _swap_deny(in_c, sw_c, reason, note):
            self.annotations.append(f"{reason}: {sw_c}->{in_c}({note})")
            plan["rejects"].append({"code": in_c, "reason": reason, "swap_out": sw_c,
                                    "score_points": sw.get("in_score")})
        for sw in plan["swaps"]:
            out_c, in_c = sw["out"], sw["in"]
            vs = [v for v in self.virtuals if v.code == out_c]
            if not vs or in_c in in_seen or swap_applied >= C.SWAP_DAILY_LIMIT:
                _swap_deny(in_c, out_c, "SWAP_SKIPPED_STATE", "无持仓/重复in/超日上限显式")
                continue
            v_out = min(vs, key=lambda v: v.risk_budget_used)
            if any(s["pos_id"] == v_out.position_id for s in sells):
                _swap_deny(in_c, out_c, "SWAP_SKIPPED_OUT_EXITING", "去重显式")
                continue
            ref_out = px.get(out_c) or self.px_last.get(out_c)
            in_p = px.get(in_c) or self.px_last.get(in_c)
            in_vol = sw.get("in_vol") or vol20.get(in_c)
            if not ref_out or ref_out <= 0 or not in_p or not in_vol:
                _swap_deny(in_c, out_c, "SWAP_SKIPPED_NO_PRICE", "显式")
                continue
            out_net = v_out.shares * ref_out - ex.fees(out_c, v_out.shares * ref_out, True)
            name_cap_val = nav_est * C.NAME_RISK_FRAC_OF_NAV / max(in_vol, 1e-9)   # 单名σ上限(同源公式)
            ind_c = sw.get("in_industry") or ind_of.get(in_c, "")
            budget = out_net
            if ind_c:
                ind_left = max(0.0, C.INDUSTRY_CONCENTRATION_ALLOWED * nav_est - ind_after.get(ind_c, 0.0))
                budget = min(budget, ind_left)            # B2修复: swap-in同样受行业60%硬顶
            budget = min(budget, name_cap_val)
            base, step = ex.lot_step(in_c)                # 统一走冻结执行器同源函数
            shares_in = int(budget / in_p)
            shares_in = base + ((shares_in - base) // step) * step if shares_in >= base else 0
            if shares_in <= 0:
                _swap_deny(in_c, out_c, "SWAP_IN_LOT_INFEASIBLE", f"budget={budget:.0f}显式")
                continue
            in_val = shares_in * in_p
            out_risk = v_out.shares * ref_out * (vol20.get(out_c) or 0.0)
            if (risk_now - out_risk + in_val * in_vol) / nav_est > C.PORT_DAILY_RISK_FRAC + 1e-9:
                _swap_deny(in_c, out_c, "SWAP_PORT_RISK_EXCEEDED", "显式")
                continue
            sw_budget = self._plan_budget(in_c, in_val, in_p, avail_pool + swap_pool + out_net)
            if sw_budget is None:                          # avail含本换仓自身卖款(BUG-2修复)
                _swap_deny(in_c, out_c, "SWAP_REVOKED_BUDGET", "显式")
                continue
            sells.append({"code": out_c, "shares": int(v_out.shares), "engine": v_out.engine,
                          "clause": f"换仓置换→{in_c}", "risk_forced": False,
                          "pos_id": v_out.position_id, "score": v_out.risk_budget_used, "kind": "SWAP"})
            buys.append({"code": in_c, "cash_budget": sw_budget,
                         "engines": sw.get("in_engines", [Engine.LONG_CYCLE.value]),
                         "score_points": sw["in_score"], "industry": ind_c,
                         "record_ids": [f"SWAP:{out_c}->{in_c}@{signal_day}"],
                         "plan_weights": [1.0] * len(sw.get("in_engines", ["x"]))})
            if ind_c:
                ind_after[ind_c] = ind_after.get(ind_c, 0.0) + in_val
            risk_now = risk_now - out_risk + in_val * in_vol
            swap_pool += out_net
            in_seen.add(in_c)
            swap_applied += 1
        # 退出优先级排序(§5.4): 风险强退 > 引擎退出 > 换仓 > 减仓 — 成交序=此序
        rank = {"FORCED": 0, "EXIT": 1, "SWAP": 2, "REDUCE": 3}
        for s_ in sells:
            s_.setdefault("kind", "FORCED" if s_.get("risk_forced") else "EXIT")
        sells.sort(key=lambda s_: (rank.get(s_.get("kind"), 9), -s_["score"]))
        # 4) 冻结执行器计划(T日收盘; created_at注入15:05北京时间窗内)
        exec_day = self._next_trading_day(signal_day)
        created = f"{signal_day}T15:05:00+08:00"
        for s in sells:
            ref = px.get(s["code"]) or self.px_last.get(s["code"])
            if ref is None:
                self.annotations.append(f"卖出计划缺参考价延迟: {s['code']}(显式)")
                continue
            pl = ex.create_plan(s["code"], "SELL", signal_day, ref, shares=s["shares"],
                                created_at=created)
            s["plan"] = pl
            self.ledger.register_plan(pl)
        for b in buys:
            ref = px.get(b["code"]) or self.px_last.get(b["code"])
            if ref is None:
                self.annotations.append(f"买入计划缺参考价放弃: {b['code']}(显式)")
                continue
            pl = ex.create_plan(b["code"], "BUY", signal_day, ref, cash_budget=b["cash_budget"],
                                created_at=created)
            b["plan"] = pl
            self.ledger.register_plan(pl)
        self.plans[signal_day] = {"sells": sells, "buys": buys, "alloc": plan,
                                  "exec_day": exec_day, "budget_state": budget_state,
                                  "dd": round(dd, 4), "cash_extra_note": cash_extra_note}
        return NetTarget(asof=signal_day,
                         targets={b["code"]: int(b["cash_budget"] // max((px.get(b["code"])
                                                       or self.px_last.get(b["code"]) or 1e-9), 1e-9))
                                  for b in buys if ("plan" in b)},
                         sells=sells, buys=buys,
                         released_cash_needed=round(est_proceeds, 2),
                         unallocated_cash=plan["cash_conservation"]["spendable_after"],
                         coverage_note=(f"budget={budget_state} dd={dd:.3f} 峰值口径; "
                                        f"同股双引擎资金单承诺; 卖先于买; {cash_extra_note}"))

    def _rebudget_pending_buys(self, rec, signal_day):
        """每笔卖出真实入账后, 把尚未执行的买入预算重估到剩余现金内(真实到账资金口径, 防缺口)。"""
        created = f"{signal_day}T15:05:00+08:00"
        avail = self.ledger.cash
        for b in rec["buys"]:
            if b.get("_executed") or "plan" not in b:
                continue
            if b["cash_budget"] > avail:
                b["cash_budget"] = round(max(avail, 0.0), 2)
                b["plan"] = ex.create_plan(b["code"], "BUY", signal_day,
                                           self.px_last.get(b["code"], b["plan"]["signal_px"]),
                                           cash_budget=b["cash_budget"], created_at=created)
                self.ledger.register_plan(b["plan"])

    # ── 执行(信息日T+1) ──
    def execute_day(self, signal_day: str, px_t1: dict, prev_close: dict, halted: set | None = None):
        halted = halted or set()
        rec = self.plans[signal_day]
        self.ledger.advance_day(rec["exec_day"])
        fills, rejects = [], []
        for s in rec["sells"]:
            if "plan" not in s:
                rejects.append({"stage": "sell", "code": s["code"], "reason": "no_plan_stale"})
                continue
            code = s["code"]
            if code in halted or code not in px_t1:
                rejects.append({"stage": "sell", "code": code, "reason": "halted_or_no_price"})
                continue
            res = ex.execute_plan(s["plan"], rec["exec_day"], px_t1[code],
                                  prev_close.get(code, px_t1[code]), halted=False)
            if res["status"] != "FILLED":
                rejects.append({"stage": "sell", "code": code, "reason": res["reason"]})
                continue
            self.ledger.apply(res)
            fills.append(res)
            self.px_last[code] = px_t1[code]
            v = next((vv for vv in self.virtuals if vv.position_id == s["pos_id"]), None)
            if v is not None:
                cost = self.pos_meta.get(v.position_id, {}).get("opened_px")
                if cost is not None:
                    self.realized_log.append({"pos_id": v.position_id, "engine": v.engine, "code": code,
                                              "shares": res["shares"],
                                              "realized": round(res["notional"] - res["fees"]
                                                                - cost * res["shares"], 2),
                                              "exec_day": res["execution_date"]})
                v.shares = max(0, v.shares - res["shares"])
                if v.shares == 0:
                    self.virtuals.remove(v)
            self._sync_real_to_virtual()
        # 缺陷修复(审查B/BUG-3): 卖→首买之间先按真实现金重估全部待买预算,
        # 消除跳空/费差导致的 Ledger 现金透支崩溃(违规路径必须显式拒单而非AssertionError)
        self._rebudget_pending_buys(rec, signal_day)
        for b in rec["buys"]:
            if "plan" not in b:
                continue
            code = b["code"]
            if code in halted or code not in px_t1:
                rejects.append({"stage": "buy", "code": code, "reason": "halted_or_no_price"})
                continue
            res = ex.execute_plan(b["plan"], rec["exec_day"], px_t1[code],
                                  prev_close.get(code, px_t1[code]), halted=False)
            if res["status"] != "FILLED":
                rejects.append({"stage": "buy", "code": code, "reason": res["reason"]})
                continue
            self.ledger.apply(res)
            fills.append(res)
            self.px_last[code] = px_t1[code]
            # 卖款即时释放: 重设后续买入预算为剩余现金内(真实到账顺序资金, 消除insufficient批量拒)
            self._rebudget_pending_buys(rec, signal_day)
            lots = self._split_lots(res["shares"], b["plan_weights"], code)
            for eng, sh in zip(b["engines"], lots):
                if sh <= 0:
                    continue
                self._seq += 1
                pid = f"V{self._seq:05d}"
                self.pos_meta[pid] = {"opened_px": res["price"]}
                self.virtuals.append(VirtualPosition(
                    position_id=pid, code=code, engine=eng, shares=sh,
                    opened_day=rec["exec_day"], entry_kind="ALLOC", thesis=f"score={b['score_points']}",
                    state="OPEN", risk_budget_used=b["score_points"]))
        self.execution_log.append({"signal_day": signal_day, "exec_day": rec["exec_day"],
                                   "fills": fills, "rejects": rejects})
        self.rejects.extend(rejects)
        return fills, rejects

    def _sync_real_to_virtual(self):
        """真实持仓与虚拟合计一致性检查(资金只承诺一次的对账): 虚>实时按引擎比例削减并显式标注。"""
        for code, p in list(self.ledger.pos.items()):
            vsum = sum(v.shares for v in self.virtuals if v.code == code)
            if vsum > p["total"]:
                excess = vsum - p["total"]
                self.annotations.append(f"虚拟超真实{excess}股({code})按比例削减(对账显式)")
                for v in sorted((v for v in self.virtuals if v.code == code), key=lambda v: -v.shares):
                    cut = min(v.shares, excess)
                    v.shares -= cut
                    excess -= cut
                    if excess <= 0:
                        break
                self.virtuals = [v for v in self.virtuals if v.shares > 0]

    def state_dict(self, px: dict) -> dict:
        return {"cash": self.ledger.cash, "nav": self.nav(px), "peak_nav": round(self.peak_nav, 4),
                "positions_real": {c: p["total"] for c, p in self.ledger.pos.items()},
                "virtuals": [{"id": v.position_id, "code": v.code, "engine": v.engine, "shares": v.shares,
                              "opened": v.opened_day} for v in self.virtuals],
                "annotations": self.annotations, "rejects_tail": self.rejects[-10:]}

    # ── 跨进程状态持久化(前向影子: T日计划与T+1日执行可分次CLI调用) ──
    def save_state(self, path: str) -> None:
        import json
        st = {"cash": self.ledger.cash, "ledger_trading_day": self.ledger.trading_day,
              "registered_plans": self.ledger.registered,
              "pos": self.ledger.pos, "peak_nav": self.peak_nav,
              "px_last": self.px_last, "pos_meta": self.pos_meta,
              "virtuals": [v.__dict__ for v in self.virtuals],
              "plans": {d: {"exec_day": r["exec_day"], "budget_state": r["budget_state"],
                            "sells": [{k: v_ for k, v_ in s.items() if k != "record"} for s in r["sells"]],
                            "buys": [{k: b_ for k, b_ in bo.items() if k != "record"} for bo in r["buys"]]}
                        for d, r in self.plans.items()},
              "seq": self._seq, "annotations": self.annotations, "realized_log": self.realized_log}
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, default=str)

    @classmethod
    def load_state(cls, path: str, nav0: float = C.NAV0, ledger_root: str | None = None,
                   ledger_name: str = "dual_cycle_v2_sim") -> "PortfolioCoordinator":
        import json
        st = json.load(open(path, encoding="utf-8"))
        co = cls(nav0=nav0, ledger_root=ledger_root, ledger_name=ledger_name)
        co.ledger.cash = st["cash"]
        co.ledger.trading_day = st["ledger_trading_day"]
        co.ledger.registered = st["registered_plans"]
        co.ledger.pos = st["pos"]
        co.peak_nav = st["peak_nav"]
        co.px_last = st["px_last"]
        co.pos_meta = st["pos_meta"]
        co.realized_log = st["realized_log"]
        co.annotations = st["annotations"]
        co._seq = st["seq"]
        co.virtuals = []
        for vd in st["virtuals"]:
            v = VirtualPosition(position_id=vd["position_id"], code=vd["code"], engine=vd["engine"],
                                shares=vd["shares"], opened_day=vd["opened_day"],
                                entry_kind=vd.get("entry_kind", "ALLOC"), thesis=vd.get("thesis", ""),
                                state=vd.get("state", "OPEN"),
                                risk_budget_used=vd.get("risk_budget_used", 0.0))
            co.virtuals.append(v)
        co.plans = {}
        for d, r in st["plans"].items():
            for s in r["sells"]:
                s["pos_id"] = s.get("pos_id", "")
            co.plans[d] = {"exec_day": r["exec_day"], "budget_state": r["budget_state"],
                           "dd": 0.0, "cash_extra_note": "", "sells": r["sells"], "buys": r["buys"],
                           "alloc": {"rejects": [], "engine_funding": {}, "cash_conservation": {}}}
        return co
