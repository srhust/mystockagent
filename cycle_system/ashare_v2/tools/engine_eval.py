# -*- coding: utf-8 -*-
"""engine_eval — 任务7: Fast-V / Long-Cycle / 合并 三对象分别评估(独立验收第7项)。

口径纪律: 只统计 覆盖度/换手/稳定性/集中度/风险 五类**行为**指标; 账本readings为模拟实现值,
不作预期收益/胜率推断(CALIBRATED=False); 历史窗口2020-2026为反复研究对象, 不构成样本外证据。
产出: output/ashare_v2/independent/engine_eval.md + engine_eval.json (可由本工具重算)。
"""
from __future__ import annotations

import csv
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)

from cycle_system.ashare_v2 import _path  # noqa: F401,E402
from cycle_system.ashare_v2 import config as C  # noqa: E402

V2 = os.path.join(ROOT, "output", "ashare_v2")


def _rows(p):
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def eval_window(run_dir: str) -> dict:
    base = os.path.join(V2, run_dir)
    opp = _rows(os.path.join(base, "opportunities.csv"))
    trades = _rows(os.path.join(base, "trades.csv"))
    planlog = _rows(os.path.join(base, "allocation_plan_log.csv"))
    nav = _rows(os.path.join(base, "nav_curve.csv"))
    state = json.load(open(os.path.join(base, "state_engine.json"), encoding="utf-8"))

    out = {}
    for eng in ("FAST_V", "LONG_CYCLE", "MERGED"):
        sel = opp if eng == "MERGED" else [r for r in opp if eng in r["engines"]]
        tr = trades if eng == "MERGED" else [r for r in trades
                                             if r.get("engine_attribution") == eng]
        # 覆盖度
        days = sorted({r["day"] for r in sel})
        codes = sorted({r["code"] for r in sel})
        n_days_all = len({r["signal_day"] for r in planlog}) or 1
        # 换手/连续性
        by_day = {}
        for r in sel:
            by_day.setdefault(r["day"], set()).add(r["code"])
        ds = sorted(by_day)
        survival = [len(by_day[a] & by_day[b]) / max(len(by_day[a]), 1)
                    for a, b in zip(ds, ds[1:])] if len(ds) > 1 else []
        # 资金/集中度
        funding = {}
        ind_w = {}
        for r in planlog:
            try:
                d = json.loads(r.get("engine_funding_json") or "{}")
            except json.JSONDecodeError:
                d = {}
            if eng != "MERGED":
                d = {eng: d.get(eng, 0.0)}
            for k, v in d.items():
                funding[k] = funding.get(k, 0.0) + float(v or 0.0)
        n_scored = sum(int(r["n_scored"]) for r in planlog)
        # 风险/拒单
        exc = sum(1 for r in planlog if r["budget_state"] == "EXCEEDED")
        exec_rej = sum(int(r["n_exec_rejects"]) for r in planlog)
        fills = sum(int(r["n_fills"]) for r in planlog)
        alloc_rej = sum(int(r["n_rejects_alloc"]) for r in planlog)
        dds = [float(r["dd_vs_peak"]) for r in planlog if r.get("dd_vs_peak")]
        buys = [r for r in tr if r["action"] == "BUY"]
        max_lot = max((float(r["notional"]) for r in buys), default=0.0)
        lot_nav_med = 0.0
        navvals = [float(r.get("nav", r.get("nav", 0))) for r in nav if "nav" in r]
        out[eng] = {
            "coverage": {
                "signal_days": len(days), "window_days": n_days_all,
                "signal_day_rate": round(len(days) / n_days_all, 3),
                "unique_codes": len(codes), "records": len(sel),
                "n_scored_total": n_scored,
            },
            "churn_stability": {
                "trades": len(tr), "buys": len(buys),
                "median_survival_d1": round(st.median(survival), 3) if survival else None,
                "lc_transitions_total": (sum(v["n_transitions"] for v in state["long_cycle"].values())
                                         if eng in ("LONG_CYCLE", "MERGED") else None),
                "fv_state_dist": state["fast_v_dist_at_day"] if eng in ("FAST_V", "MERGED") else None,
            },
            "concentration": {
                "funding_by_engine": {k: round(v, 1) for k, v in funding.items()},
                "max_single_buy_notional": round(max_lot, 1),
            },
            "risk_discipline": {
                "exceeded_days": exc, "alloc_rejects": alloc_rej,
                "exec_fills": fills, "exec_rejects": exec_rej,
                "exec_reject_rate": round(exec_rej / (fills + exec_rej), 3) if fills + exec_rej else None,
                "max_dd_seen": round(min(dds), 4) if dds else None,
            },
        }
    return out


def main() -> int:
    res = {"shadow_run_realdata": eval_window("shadow_run"),
           "long_backtest": eval_window("long_backtest"),
           "synthetic_run": eval_window("synthetic_run"),
           "disclaimer": "行为指标评估, 非收益宣称; 账本数值为模拟实现读数(含费), "
                         "不作预期收益/胜率外推(CALIBRATED=False); "
                         "历史窗口2020-2026已被反复研究, 仅REPLAY证据。",
           "config_hash": C.config_hash()}
    odir = os.path.join(V2, "independent")
    os.makedirs(odir, exist_ok=True)
    with open(os.path.join(odir, "engine_eval.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False)[:400], "...(full in engine_eval.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
