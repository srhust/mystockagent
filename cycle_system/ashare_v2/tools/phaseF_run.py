# -*- coding: utf-8 -*-
"""阶段F: 三模式全流程运行 + §10十产物。

用法: python cycle_system/ashare_v2/tools/phaseF_run.py [diagnostic|synthetic|shadow|all]
输出: output/ashare_v2/diagnostic_run/ synthetic_run/ shadow_run/
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                   # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                             # noqa: E402
from cycle_system.ashare_v2 import report as RP                            # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                  # noqa: E402
from cycle_system.ashare_v2.runner import Runner                           # noqa: E402
from cycle_system.ashare_v2.schemas import RunMode                         # noqa: E402

rng = np.random.default_rng(42)


def synth_panel():
    """§9.2 A-H 内置合成面板(日历过滤交易日位点, 无假日断档)。"""
    from cycle_system import ashare_calendar as cal
    all_bd = pd.bdate_range("2022-06-01", "2026-06-30")
    dates = [d for d in all_bd if cal.is_trading_day(d)]
    n = len(dates)
    crash_i, bottom_i = int(n * 0.72), int(n * 0.80)

    def downtrend_then_bottom(start, k):
        px = np.linspace(start, start * 0.5, bottom_i)
        seg = px[bottom_i - 30]
        rest = seg * (1 + 0.0009 * np.arange(n - bottom_i))
        return np.concatenate([px, rest])

    def flat_then_crash_rebound(start):
        px = np.full(n, start, dtype=float)
        px[:crash_i] = start * (1 - 0.00015 * np.arange(crash_i))
        top = px[crash_i - 1]
        for i in range(20):
            px[crash_i + i] = top * (1 - 0.35 * (i + 1) / 20)
        low = px[crash_i + 19]
        for i in range(1, 31):
            px[crash_i + 20 + i - 1] = low * (1 + 0.30 * i / 30)
        px[crash_i + 50:] = low * 1.30
        return px

    def up_then_ma20_dip(start):
        px = start * np.exp(np.linspace(0, 0.9, n))
        px[crash_i:crash_i + 8] = px[crash_i] * (1 - 0.02 * np.arange(8))   # 短暂破MA20
        px[crash_i + 8:] = px[crash_i + 7] * (1 + 0.001 * np.arange(n - crash_i - 8))
        return px

    def crash_then_break_low(start):
        px = flat_then_crash_rebound(start)
        j = crash_i + 50
        px[j:] = px[j - 1] * (1 - 0.006 * np.arange(n - j))                  # 假反弹后跌破前低
        return px

    def high_vol(start):
        steps = rng.normal(0.0002, 0.06, n)
        return start * np.exp(np.cumsum(steps))

    def frames():
        out = []
        a1 = downtrend_then_bottom(80, 1); a2 = downtrend_then_bottom(40, 2) * 2
        b1 = flat_then_crash_rebound(30)
        c1 = up_then_ma20_dip(20)
        d1 = crash_then_break_low(25)
        f1 = downtrend_then_bottom(60, 6); f2 = up_then_ma20_dip(50)
        g1 = downtrend_then_bottom(70, 8); g2 = downtrend_then_bottom(35, 9) * 2
        h1 = high_vol(45)
        yield ("SA001", "IND_A", a1, 8e7)
        yield ("SA002", "IND_A", a2, 6e7)
        yield ("SB001", "IND_B", b1, 9e7)
        yield ("SC001", "IND_C", c1, 7e7)
        yield ("SD001", "IND_D", d1, 8e7)
        yield ("SF001", "IND_F", f1, 6e7)
        yield ("SF002", "IND_F", f2, 9e7)
        yield ("SG001", "IND_G", g1, 5e7)
        yield ("SG002", "IND_G", g2, 5e7)
        yield ("SH001", "IND_H", h1, 4e7)
    rows = []
    for code, sector, px, amt in frames():
        px = np.asarray(px, dtype=float)[:n]
        noise = 1 + rng.normal(0, 0.004, n)
        px = np.maximum(px * (noise if code == "SH001" else noise * 0.2 + 0.8), 0.5)
        for i, d in enumerate(dates):
            rows.append({"date": d, "code": code, "sector": sector, "open": px[i], "high": px[i],
                         "low": px[i], "close": px[i], "volume": amt / max(px[i], 1e-6),
                         "amount": amt, "adj_factor": 1.0, "close_adj": px[i]})
    df = pd.DataFrame(rows).sort_values(["date", "code"]).reset_index(drop=True)
    return df


class SynthPortal:
    def __init__(self, df):
        self._df = df

    def price_panel(self):
        return self._df

    def quality_report(self):
        return {"note": "SYNTHETIC合成面板(§9.2 A-H内置), 非真实行情", "rows": int(len(self._df)),
                "codes": int(self._df["code"].nunique())}


def run_diagnostic():
    r = Runner(RunMode.DIAGNOSTIC.value)
    r.setup()
    diag = r.diagnose("2026-06-30")
    outdir = os.path.join(C.OUTPUT_DIR, "diagnostic_run")
    files = RP.write_all(outdir, r, None, diag=diag)
    return {"mode": "DIAGNOSTIC", "files": files, "candidates": diag["candidates_merged"],
            "hypothetical_buys": len(diag["hypothetical_allocation"]["buys"]),
            "strict_gate": diag["strict_gate"] and diag["strict_gate"][0]["status"]}


def run_synthetic():
    r = Runner(RunMode.SYNTHETIC.value, portal=SynthPortal(synth_panel()), ledger_root=os.path.join(
        C.OUTPUT_DIR, "synthetic_run", "ledger"), ledger_name="dual_cycle_v2_synth")
    res = r.run()
    files = RP.write_all(os.path.join(C.OUTPUT_DIR, "synthetic_run"), r, res)
    slim = {k: res[k] for k in ("mode", "window", "days_simulated", "nav_final", "cash_final",
                                "conservation_ok", "virtual_real_reconciled", "n_fills", "n_rejects")}
    return {"mode": "SYNTHETIC", "files": files, **slim, "annotations": res["annotations"][:5]}


def run_shadow():
    portal = DataPortal(data_mode=C.PIT_RESEARCH)
    r = Runner(RunMode.SHADOW.value, portal=portal, window=("2026-03-02", "2026-06-30"),
               ledger_root=os.path.join(C.OUTPUT_DIR, "shadow_run", "ledger"),
               ledger_name="dual_cycle_v2_shadow")
    res = r.run()
    files = RP.write_all(os.path.join(C.OUTPUT_DIR, "shadow_run"), r, res)
    slim = {k: res[k] for k in ("mode", "window", "days_simulated", "nav_final", "cash_final",
                                "conservation_ok", "virtual_real_reconciled", "n_fills", "n_rejects")}
    return {"mode": "SHADOW", "files": files, **slim,
            "reject_reasons_top": _top_reasons(r), "annotations": res["annotations"][:5]}


def _top_reasons(r):
    cnt = {}
    for x in r.coord.execution_log:
        for j in x["rejects"]:
            cnt[j["reason"]] = cnt.get(j["reason"], 0) + 1
    return cnt


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = []
    if which in ("diagnostic", "all"):
        out.append(run_diagnostic())
    if which in ("synthetic", "all"):
        out.append(run_synthetic())
    if which in ("shadow", "all"):
        out.append(run_shadow())
    print(json.dumps(out, ensure_ascii=False, default=str, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
