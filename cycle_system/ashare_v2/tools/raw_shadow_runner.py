# -*- coding: utf-8 -*-
"""raw_shadow_runner — 阶段1 P0回测修复的落地入口(tools域子类, 16预测模块零改动)。

价格纪律(审查第三轮指令):
  信号: close_adj 域(引擎输入原样, _exit_ctx/vol20 不动) —— 预测规则零触碰;
  交易账本: T日 raw close 做资金预算与整手, T+1 **raw open** 成交, raw close 估值,
            涨跌停/费用全 raw 域(执行器原生口径);
  股数绝不按 adj_factor 逐日换算(hfq基准滚动漂移会失真, adj_factor_check铁证);
  双轨呈报: raw NAV(主) + adj NAV(对照) + 差额=除权除息结构性缺口的显式标注。

审计字段(指令1-5): 每笔成交/拒单落 trade_audit.csv(signal_day/成交raw开/估值raw收/
adj收/cash_delta/plan_hash/拒单原因/当日现金与持仓)。

用法: python -X utf8 tools/raw_shadow_runner.py <start> <end> [--engines LONG_CYCLE|FAST_V]
输出: output/ashare_v2/raw_bt/{start}_{end}[_engine]/  隔离新账本名(不与其他run共享文件)。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                              # noqa: E402
from cycle_system.ashare_v2 import report as RP                             # noqa: E402
from cycle_system.ashare_v2 import signal_merge as M                         # noqa: E402
from cycle_system.ashare_v2 import opportunity_ranker as R                   # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                    # noqa: E402
from cycle_system.ashare_v2.runner import Runner                             # noqa: E402
from cycle_system.ashare_v2.schemas import Engine, RunMode                   # noqa: E402


class RawRunner(Runner):
    def __init__(self, *a, allow_engines: str | None = None, **kw):
        super().__init__(*a, **kw)
        self.allow_engines = allow_engines
        p = self.panel if hasattr(self, "panel") else None
        panel = self.portal.price_panel()
        self.raw_open = panel.pivot(index="date", columns="code", values="open")
        self.raw_close = panel.pivot(index="date", columns="code", values="close")
        self.trade_audit: list = []

    # ── 信号域=adj(引擎原样) + 引擎过滤(阶段2三模式) ──
    def _scored_for(self, d):
        if self.allow_engines is None:
            return super()._scored_for(d)
        day = str(pd.Timestamp(d).date())
        lc_c = self.lc.candidates(day)
        fv_c = self.fv.candidates(day)
        if self.allow_engines == Engine.LONG_CYCLE.value:
            fv_c = []
        if self.allow_engines == Engine.FAST_V.value:
            lc_c = []
        merged = M.merge_candidates(lc_c, fv_c)
        _, _, amtm = self._day_maps(d)
        for m in merged:
            age_d = {}
            for eng in m["engines"]:
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

    @staticmethod
    def _row(df, d):
        if d not in df.index:
            return {}
        out = df.loc[d]
        return {c: (float(v) if pd.notna(v) and np.isfinite(v) else None) for c, v in out.items()}

    def _anchor_adj(self):
        """T0日因子锚: adj_norm=close_adj/anchor → 与raw同水平, 轨差只反映除权除息效应。"""
        d0 = self.dates[0]
        a = {}
        for c in self.raw_close.columns:
            try:
                p_raw = float(self.raw_close.loc[d0, c]); p_adj = float(self.px.loc[d0, c])
            except (KeyError, ValueError):
                continue
            if p_raw and p_adj and np.isfinite(p_raw) and np.isfinite(p_adj):
                a[c] = p_adj / p_raw
        return a

    def run(self) -> dict:
        self.setup()
        anchor = self._anchor_adj()
        days = self.dates
        for k in range(len(days) - 1):
            d, d1 = days[k], days[k + 1]
            day_s = str(pd.Timestamp(d).date())
            scored = self._scored_for(d)
            _pxm_adj, volm, _ = self._day_maps(d)                 # vol20仍是adj域(风险信号)
            rawc_d = {c: v for c, v in self._row(self.raw_close, d).items() if v}
            ctx_vol = {c: v for c, v in volm.items() if v and rawc_d.get(c)}
            net = self.coord.plan_day(day_s, [s for s in scored if s["code"] in rawc_d],
                                      rawc_d, ctx_vol, self.sec_of,
                                      exit_ctx_fn=lambda c, dd, _d=d: self._exit_ctx(c, _d))
            rawo_1 = {c: v for c, v in self._row(self.raw_open, d1).items() if v}
            rawc_1 = {c: v for c, v in self._row(self.raw_close, d1).items() if v}
            fills, rejects = self.coord.execute_day(day_s, rawo_1, rawc_d)   # 成交=raw open
            rec = self.coord.plans[day_s]
            if rec["exec_day"] != str(pd.Timestamp(d1).date()):
                raise RuntimeError(f"面板日序失配: {day_s} exec={rec['exec_day']} ≠ {d1}")
            for f in fills:
                if f.get("status") != "FILLED":
                    self.trade_audit.append({
                        "signal_day": day_s, "exec_day": rec["exec_day"], "code": f["code"],
                        "action": "REJECT_exec", "plan_hash": f.get("plan_hash", ""),
                        "shares": 0, "exec_raw_open": rawo_1.get(f["code"]),
                        "val_raw_close": rawc_1.get(f["code"]), "val_adj_close": (self._row(self.px, d1).get(f["code"])),
                        "prev_raw_close": rawc_d.get(f["code"]), "fees": None,
                        "cash_delta": 0.0, "origin": str(f.get("reason"))})
                    continue
                self.trade_audit.append({
                    "signal_day": day_s, "exec_day": rec["exec_day"], "code": f["code"],
                    "action": f["action"], "plan_hash": f.get("plan_hash", ""),
                    "shares": f["shares"], "exec_raw_open": f["price"],
                    "val_raw_close": rawc_1.get(f["code"]), "val_adj_close": (self._row(self.px, d1).get(f["code"])),
                    "prev_raw_close": rawc_d.get(f["code"]), "fees": f.get("fees"),
                    "cash_delta": f.get("cash_delta"), "origin": "RAW_OPEN"})
            for rj in rejects:
                self.trade_audit.append({
                    "signal_day": day_s, "exec_day": rec["exec_day"], "code": rj.get("code"),
                    "action": "REJECT_" + str(rj.get("stage", "")), "plan_hash": rj.get("parent_plan_hash", ""),
                    "shares": 0, "exec_raw_open": rawo_1.get(rj.get("code")),
                    "val_raw_close": rawc_1.get(rj.get("code")), "val_adj_close": None,
                    "prev_raw_close": rawc_d.get(rj.get("code")), "fees": None,
                    "cash_delta": 0.0, "origin": str(rj.get("reason"))})
            nav_raw = self.coord.nav(rawc_1)                                   # raw close估值
            pos = {c: p["total"] for c, p in self.coord.ledger.pos.items()}
            adj_row = self._row(self.px, d1)
            nav_adj = self.coord.ledger.cash + sum(
                sh * ((adj_row.get(c) or 0.0) / anchor[c]) for c, sh in pos.items() if c in anchor)
            self.nav_hist.append({"day": rec["exec_day"], "nav": nav_raw, "cash": round(self.coord.ledger.cash, 4),
                                  "positions": pos, "nav_adj_track": round(nav_adj, 4),
                                  "budget_state": rec["budget_state"]})
            self.plan_log.append({"signal_day": day_s, "exec_day": rec["exec_day"],
                                  "n_scored": len(scored), "n_buys": len(net.buys), "n_sells": len(net.sells),
                                  "n_rejects_alloc": len(rec["alloc"]["rejects"]), "budget_state": rec["budget_state"],
                                  "dd": rec["dd"], "engine_funding": rec["alloc"]["engine_funding"],
                                  "cash_conservation": rec["alloc"]["cash_conservation"],
                                  "n_fills": len(fills), "n_exec_rejects": len(rejects)})
        res = super_run_tail(self, days)
        res["mode"] = f"RAW_{self.mode}" + (f"_{self.allow_engines}" if self.allow_engines else "")
        return res


def super_run_tail(r, days):
    last_raw = {c: v for c, v in r._row(r.raw_close, days[-1]).items() if v}
    nav_final = r.coord.nav(last_raw)
    pos_val = nav_final - r.coord.ledger.cash
    ok = abs(nav_final - (r.coord.ledger.cash + pos_val)) < 1e-6
    vsum = {}
    for v in r.coord.virtuals:
        vsum[v.code] = vsum.get(v.code, 0) + v.shares
    recon = all(vsum.get(c, 0) <= p["total"] + 1e-9 for c, p in r.coord.ledger.pos.items()) \
        and all(p["total"] == vsum.get(c, 0) for c, p in r.coord.ledger.pos.items())
    return {"mode": r.mode, "window": (str(pd.Timestamp(days[0]).date()), str(pd.Timestamp(days[-1]).date())),
            "days_simulated": len(days) - 1, "nav_hist": r.nav_hist, "plan_log": r.plan_log,
            "nav_final": nav_final, "cash_final": round(r.coord.ledger.cash, 4),
            "conservation_ok": ok, "virtual_real_reconciled": recon,
            "n_fills": sum(x["n_fills"] for x in r.plan_log),
            "n_rejects": sum(x["n_exec_rejects"] for x in r.plan_log),
            "trade_audit": r.trade_audit,
            "annotations": getattr(r.coord, "annotations", [])}


def main() -> int:
    start = sys.argv[1] if len(sys.argv) > 1 else "2026-03-02"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-06-30"
    eng = None
    if "--engines" in sys.argv:
        eng = sys.argv[sys.argv.index("--engines") + 1]
    tag = f"{start}_{end}" + (f"_{eng}" if eng else "")
    outdir = os.path.join(C.OUTPUT_DIR, "raw_bt", tag)
    r = RawRunner(RunMode.SHADOW.value, portal=DataPortal(data_mode=C.PIT_RESEARCH),
                  window=(start, end), allow_engines=eng,
                  ledger_root=os.path.join(outdir, "ledger"), ledger_name=f"raw_{tag.replace('-', '')}")
    res = r.run()
    files = RP.write_all(outdir, r, res)
    pd.DataFrame([{k: v for k, v in h.items() if k != "positions"} for h in res["nav_hist"]]
                 ).to_csv(os.path.join(outdir, "nav_hist_full.csv"),
                          index=False, encoding="utf-8-sig")   # adj对照轨全列(拍平positions)
    pd.DataFrame(res.get("trade_audit", [])).to_csv(os.path.join(outdir, "trade_audit.csv"),
                                                    index=False, encoding="utf-8-sig")
    print(json.dumps({"mode": res["mode"], "window": res["window"], "files": files + ["trade_audit.csv"],
                      "days_simulated": res["days_simulated"], "nav_final_raw": res["nav_final"],
                      "cash_final": res["cash_final"], "conservation_ok": res["conservation_ok"],
                      "virtual_real_reconciled": res["virtual_real_reconciled"],
                      "n_fills": res["n_fills"], "n_rejects": res["n_rejects"],
                      "note": "RAW交易域(成交=open/估值=close原始价)+adj对照轨; REPLAY非前向, 非性能结论"},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
