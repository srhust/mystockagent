# -*- coding: utf-8 -*-
"""runner — 三模式主循环 (阶段F实装, §10/§12)。

  * DIAGNOSTIC: 单日截面诊断——状态/候选/假想分配计划, **零执行零账本**;
  * SYNTHETIC : 合成面板多日模拟(§9.2 A-H 场景内置)——验证协调器/账本/守恒机制, 结果
                仅证明"系统按规则运行", 非市场预测;
  * SHADOW    : 真实 R31 数据面板多日模拟——成交走冻结执行器+隔离影子账本
                (output/ashare_v2/ledger_shadow/), 绝不触碰实盘/旧账本; 显式标注
                非样本外(2020-2026研究窗)/非预测有效。
数据模式: 模拟统一 PIT_RESEARCH(候选带 PROXY/信任标注——SW历史成员结构性缺口使 STRICT 无正式
信号可模拟, 该闸门本身由 test_phaseC 锁定)。评分未校准 → 全流程无收益率/胜率话术。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from cycle_system.ashare_v2 import config as C
from cycle_system.ashare_v2 import capital_allocator as K
from cycle_system.ashare_v2 import opportunity_ranker as R
from cycle_system.ashare_v2 import signal_merge as M
from cycle_system.ashare_v2 import data_portal as _DP  # noqa: F401  (保持导入序稳定)
from cycle_system.ashare_v2 import exit_manager as EM
from cycle_system.ashare_v2.data_portal import DataPortal
from cycle_system.ashare_v2.fast_v import FastVEngine
from cycle_system.ashare_v2.industry_cycle import (IndustryMembershipProvider, IndustrySeriesBuilder)
from cycle_system.ashare_v2.long_cycle import LongCycleEngine
from cycle_system.ashare_v2.portfolio import PortfolioCoordinator
from cycle_system.ashare_v2.schemas import DataMode, Engine, RunMode

TZ = timezone(timedelta(hours=8))


class Runner:
    def __init__(self, mode: str, portal=None, window=None, nav0: float = C.NAV0,
                 ledger_root: str = None, ledger_name: str = "dual_cycle_v2_sim"):
        if mode not in (RunMode.DIAGNOSTIC.value, RunMode.SYNTHETIC.value, RunMode.SHADOW.value):
            raise ValueError(f"未知模式: {mode}")
        self.mode = mode
        self.portal = portal if portal is not None else DataPortal(data_mode=C.PIT_RESEARCH)
        self.window = window
        self.nav0 = nav0
        self.ledger_root = ledger_root
        self.ledger_name = ledger_name
        self.coord = None
        self.opportunities: list = []
        self.plan_log: list = []
        self.nav_hist: list = []
        self.strict_gate_tail: list = []

    # ── 设置 ──
    def setup(self):
        panel = self.portal.price_panel()
        self.panel = panel
        self.dates = list(panel["date"].unique())
        w = self.window
        if w:
            self.dates = [d for d in self.dates if pd.Timestamp(w[0]) <= d <= pd.Timestamp(w[1])]
        self.sec_of = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()
        self.px = panel.pivot(index="date", columns="code", values="close_adj")
        self.rawpx = panel.pivot(index="date", columns="code", values="close")
        r = self.px.pct_change()
        self.vol = r.rolling(20, min_periods=10).std()
        self.amt20 = panel.pivot(index="date", columns="code", values="amount").rolling(20, min_periods=10).mean()
        self.b = IndustrySeriesBuilder(self.portal)
        self.mp = IndustryMembershipProvider()
        self.lc = LongCycleEngine(self.portal, self.b, self.mp, data_mode=C.PIT_RESEARCH)
        self.lc.build()
        self.fv = FastVEngine(self.portal, data_mode=C.PIT_RESEARCH)
        self.fv.build()
        if self.mode != RunMode.DIAGNOSTIC.value:
            self.coord = PortfolioCoordinator(nav0=self.nav0, ledger_root=self.ledger_root,
                                              ledger_name=self.ledger_name)

    # ── 查询辅助 ──
    def _day_maps(self, d):
        i = self.px.index.get_loc(d)
        def row(df):
            out = df.iloc[i]
            return {c: (float(v) if pd.notna(v) and np.isfinite(v) else None) for c, v in out.items()}
        return row(self.px), row(self.vol), row(self.amt20)

    def _exit_ctx(self, code, d):
        ctx = {}
        ind = self.sec_of.get(code, "")
        if ind in self.lc._states:
            ctx["industry_state"] = self.lc.industry_state(ind, str(pd.Timestamp(d).date()))
        ctx["fast_v_state"] = self.fv.state_at(code, str(pd.Timestamp(d).date()))
        ctx["rebound_fade"] = self.fv.fade_at(code, str(pd.Timestamp(d).date()))
        st = self.lc._stk_tables.get(code)
        ft = self.lc._ind_tables.get(ind)
        if st is not None and d in st.index:
            row = st.loc[d]
            if pd.notna(row["ma20"]):
                ctx["ma20_break"] = bool(row["px"] < row["ma20"])
        if st is not None and ft is not None and d in st.index and d in ft.index:
            r60 = st.loc[d].get("trend60")
            i60 = ft.loc[d].get("trend60")
            if pd.notna(r60) and pd.notna(i60):
                rel = float(r60) - float(i60)
                ctx["rel_industry_60"] = rel
                if rel <= EM.LC_WEAK_REDUCE:
                    cnt = 0
                    for j in range(len(ft) - 1, -1, -1):
                        dd = ft.index[j]
                        if dd > d:
                            continue
                        rs, fi = st.get("trend60").get(dd), ft["trend60"].iloc[j]
                        if pd.notna(rs) and pd.notna(fi) and float(rs) - float(fi) <= EM.LC_WEAK_REDUCE:
                            cnt += 1
                        else:
                            break
                    ctx["rel_weak_days"] = cnt
        return ctx

    def _scored_for(self, d):
        day = str(pd.Timestamp(d).date())
        merged = M.merge_candidates(self.lc.candidates(day), self.fv.candidates(day))
        _, _, amtm = self._day_maps(d)
        for m in merged:
            age_d = {}
            engs = m["engines"]
            for eng in engs:
                first = None
                if eng == Engine.LONG_CYCLE.value:
                    ds = [v for (c, e), v in self.lc._first_elig.items() if c == m["code"]]
                    first = min(ds) if ds else day
                else:
                    first = self.fv._first_sig.get(m["code"], day)
                age = (pd.Timestamp(day) - pd.Timestamp(first)).days
                age_d[eng] = {"amt20": amtm.get(m["code"]),
                              "freshness": max(0.0, 1 - min(1.0, age / 60.0))}
            m["age_days"] = age_d
        scored = []
        for m in merged:
            s = R.score_merged(m)
            s["industry"] = self.sec_of.get(m["code"], "")
            scored.append(s)
            self.opportunities.append({"day": day, "merged": m, "score": s})
        return scored

    # ── 单日诊断 ──
    def diagnose(self, day: str) -> dict:
        if self.coord is None and self.mode != RunMode.DIAGNOSTIC.value:
            raise RuntimeError("diagnose仅限DIAGNOSTIC模式或只读路径")
        pxm, volm, _ = self._day_maps(pd.Timestamp(day))
        scored = self._scored_for(pd.Timestamp(day))
        plan = K.allocate([s for s in scored if volm.get(s["code"]) and pxm.get(s["code"])],
                          [], cash=self.nav0, nav=self.nav0,
                          ctx={"px": pxm, "vol20": {k: v for k, v in volm.items() if v},
                               "industry": {s["code"]: s["industry"] for s in scored},
                               "budget_state": "NORMAL"})
        gate = []
        if self.mode == RunMode.DIAGNOSTIC.value:
            strict = LongCycleEngine(self.portal, IndustrySeriesBuilder(self.portal), self.mp,
                                     data_mode=C.PIT_STRICT)
            strict.build()
            strict.candidates(day)
            gate = strict.strict_gate_report[-1:]
        return {"mode": self.mode, "day": day,
                "industry_states": {ind: self.lc.industry_state(ind, day) for ind in self.lc._states},
                "fast_v_states": {c: self.fv.state_at(c, day) for c in self.fv._states},
                "candidates_merged": len(scored), "opportunity_samples": scored[:10],
                "hypothetical_allocation": plan, "strict_gate": gate}

    # ── 多日模拟(SYNTHETIC/SHADOW) ──
    def run(self) -> dict:
        self.setup()
        if self.mode == RunMode.DIAGNOSTIC.value:
            raise RuntimeError("DIAGNOSTIC模式请用diagnose(day); 零执行零账本")
        days = self.dates
        for k in range(len(days) - 1):
            d, d1 = days[k], days[k + 1]
            day_s = str(pd.Timestamp(d).date())
            scored = self._scored_for(d)
            pxm, volm, _ = self._day_maps(d)
            ctx_vol = {c: v for c, v in volm.items() if v and pxm.get(c)}
            px_valid = {c: v for c, v in pxm.items() if v}
            net = self.coord.plan_day(day_s, [s for s in scored if s["code"] in px_valid],
                                      px_valid, ctx_vol, self.sec_of,
                                      exit_ctx_fn=lambda c, dd, _d=d: self._exit_ctx(c, _d))
            px1m, _, _ = self._day_maps(d1)
            px1 = {c: v for c, v in px1m.items() if v}
            fills, rejects = self.coord.execute_day(day_s, px1, px_valid)
            rec = self.coord.plans[day_s]
            # 缺陷修复(审查A): 面板相邻两行必须是日历上的相邻交易日, 否则T+1语义错位
            if rec["exec_day"] != str(pd.Timestamp(d1).date()):
                raise RuntimeError(f"面板日序与交易日历失配: 信号{day_s}的exec_day={rec['exec_day']}"
                                   f" ≠ 面板次日{d1}(合成面板必须按日历过滤, §12)")
            self.plan_log.append({"signal_day": day_s, "exec_day": rec["exec_day"],
                                  "n_scored": len(scored), "n_buys": len(net.buys),
                                  "n_sells": len(net.sells), "n_rejects_alloc": len(rec["alloc"]["rejects"]),
                                  "budget_state": rec["budget_state"], "dd": rec["dd"],
                                  "engine_funding": rec["alloc"]["engine_funding"],
                                  "cash_conservation": rec["alloc"]["cash_conservation"],
                                  "n_fills": len(fills), "n_exec_rejects": len(rejects)})
            self.nav_hist.append({"day": rec["exec_day"], "nav": self.coord.nav(px1),
                                  "cash": round(self.coord.ledger.cash, 4),
                                  "positions": {c: p["total"] for c, p in self.coord.ledger.pos.items()}})
        last_px = self._day_maps(days[-1])[0]
        nav_final = self.coord.nav(last_px)
        pos_val = nav_final - self.coord.ledger.cash
        ok = abs(nav_final - (self.coord.ledger.cash + pos_val)) < 1e-6
        vsum = {}
        for v in self.coord.virtuals:
            vsum[v.code] = vsum.get(v.code, 0) + v.shares
        recon = all(vsum.get(c, 0) <= p["total"] + 1e-9 for c, p in self.coord.ledger.pos.items()) \
            and all(p["total"] == vsum.get(c, 0) for c, p in self.coord.ledger.pos.items())
        return {"mode": self.mode, "window": (str(pd.Timestamp(days[0]).date()), str(pd.Timestamp(days[-1]).date())),
                "days_simulated": len(days) - 1, "nav_hist": self.nav_hist, "plan_log": self.plan_log,
                "nav_final": nav_final, "cash_final": round(self.coord.ledger.cash, 4),
                "conservation_ok": ok, "virtual_real_reconciled": recon,
                "n_fills": sum(len(x["fills"]) for x in self.coord.execution_log),
                "n_rejects": len(self.coord.rejects), "coord": self.coord,
                "annotations": self.coord.annotations,
                "realized_log": self.coord.realized_log}
