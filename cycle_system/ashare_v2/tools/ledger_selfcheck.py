# -*- coding: utf-8 -*-
"""ledger_selfcheck — 验收第一关: NAV/持仓/现金账本三方自洽校验器。

对任一 REPLAY run 目录(shadow_run/long_backtest/rehearsal等)独立重算并交叉核对:
  C1 现金流水: nav0 + Σ(ledger jsonl cash_delta by 日) == nav_curve 每日 cash 列
  C2 NAV构成: nav == cash + Σ(持仓股数 × 估值价(指定价格域)) 逐日
  C3 成交一致: trades.csv 与 ledger jsonl 逐笔对齐(plan_hash/股数/名义额)
  C4 股数守恒: 每 code 累计买入股数 - 卖出股数 == 当日 positions_json 存量
价格域参数: adj=close_adj(旧SHADOW) / raw=close(RAW重建版, 阶段1修复后)。
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                              # noqa: E402


def check(run_dir: str, price_basis: str = "adj", tol: float = 0.51) -> dict:
    panel_col = "close_adj" if price_basis == "adj" else "close"
    out = {"run": os.path.basename(run_dir), "price_basis": price_basis, "checks": {}}
    nav = pd.read_csv(os.path.join(run_dir, "nav_curve.csv"), encoding="utf-8-sig")
    led_p = None
    for root, _dirs, files in os.walk(run_dir):
        for f in files:
            if f.startswith("ledger_") and f.endswith(".jsonl"):
                led_p = os.path.join(root, f)
    if not led_p:
        out["checks"]["C0_ledger_present"] = {"ok": False, "note": "无账本jsonl(零成交run合法, C1-C4空真)"}
        return out
    fills = pd.DataFrame([json.loads(l) for l in open(led_p, encoding="utf-8")])
    from cycle_system.ashare_v2.data_portal import DataPortal
    panel = DataPortal(data_mode=C.PIT_RESEARCH).price_panel()
    px = panel.pivot(index="date", columns="code", values=panel_col)

    # CAI.5 守恒扩展: ca_events.csv 存在则纳入(缺文件=Σ0, 既有run逐数不变)
    div_by_day: dict = {}
    bonus_by_day_code: dict = {}
    ca_p = os.path.join(run_dir, "ca_events.csv")
    if os.path.exists(ca_p):
        for _, r in pd.read_csv(ca_p, dtype=str).iterrows():
            if r["type"] == "DIVIDEND" and r["tax_chosen"] != "NO_HOLDER":
                div_by_day[str(r["day"])] = div_by_day.get(str(r["day"]), 0.0) + float(r["amount"])
            elif r["type"] in ("BONUS", "SPLIT"):
                key = (str(r["day"]), str(r["code"]))
                bonus_by_day_code[key] = bonus_by_day_code.get(key, 0.0) + float(r["amount"])

    # C1 现金流水逐日重放
    cash = C.NAV0
    delta_by_day: dict = {}
    for _, r in fills.iterrows():
        delta_by_day[str(r["execution_date"])] = delta_by_day.get(str(r["execution_date"]), 0.0) + float(r["cash_delta"])
    bad_c1 = []
    for _, row in nav.iterrows():
        d = str(row["day"])
        cash += delta_by_day.get(d, 0.0) + div_by_day.get(d, 0.0)      # CAI.5: +Σ当日派息审计
        if abs(cash - float(row["cash"])) > tol:
            bad_c1.append({"day": d, "recomputed_cash": round(cash, 2), "recorded": float(row["cash"])})
    # C4 股数守恒(逐日重放持仓)
    held: dict = {}
    bad_c4 = []
    for _, row in nav.iterrows():
        d = str(row["day"])
        for _, r in fills.iterrows():
            if str(r["execution_date"]) == d:
                sgn = 1 if r["action"] == "BUY" else -1
                held[str(r["code"])] = held.get(str(r["code"]), 0) + sgn * int(r["shares"])
        for (bd, bcode), dq in bonus_by_day_code.items():               # CAI.5: BONUS股差入held
            if bd == d:
                held[str(bcode)] = held.get(str(bcode), 0) + int(dq)
        try:
            pos = json.loads(row["positions_json"]) if isinstance(row["positions_json"], str) else {}
        except (TypeError, json.JSONDecodeError):
            pos = {}
        pos = {str(k): int(v) for k, v in (pos or {}).items() if int(v)}
        diff = {k: (held.get(k, 0) - pos.get(k, 0)) for k in set(held) | set(pos) if held.get(k, 0) != pos.get(k, 0)}
        if diff:
            bad_c4.append({"day": d, "diff": diff})
    bad_c2 = []
    last_px = {}          # 停牌携带价缓存(校验侧, 与账本px_last同源语义)
    cash2 = C.NAV0
    for _, row in nav.iterrows():
        d = str(row["day"])
        cash2 += delta_by_day.get(d, 0.0) + div_by_day.get(d, 0.0)       # CAI.5 同步C2
        try:
            pos = json.loads(row["positions_json"]) if isinstance(row["positions_json"], str) else {}
        except (TypeError, json.JSONDecodeError):
            pos = {}
        mv = 0.0
        for code, sh in {str(k): int(v) for k, v in (pos or {}).items() if int(v)}.items():
            try:
                p = float(px.loc[pd.Timestamp(d), code])
                if pd.isna(p):
                    p = last_px.get(code)     # 停牌: 与coord.nav的px_last估值语义同源
                    if p is None:
                        continue
            except KeyError:
                p = last_px.get(code)         # 停牌整行缺失: 同上(校验侧盲区修复, 非账本缺陷)
                if p is None:
                    continue
            else:
                last_px[code] = p
            mv += sh * p
        if abs(float(row["nav"]) - (cash2 + mv)) > 1.0:
            bad_c2.append({"day": d, "nav": float(row["nav"]), "recomputed": round(cash2 + mv, 2)})
    # C3 trades.csv 对齐
    bad_c3 = []
    tp = os.path.join(run_dir, "trades.csv")
    if os.path.exists(tp):
        t = pd.read_csv(tp, encoding="utf-8-sig", dtype={"code": str})
        key_t = {(r["exec_day"], str(r["code"]).zfill(6), r["action"]): (int(r["shares"]), round(float(r["notional"]), 1))
                 for _, r in t[t["action"].isin(["BUY", "SELL"])].iterrows()}
        key_l = {(str(r["execution_date"]), str(r["code"]).zfill(6), r["action"]): (int(r["shares"]), round(float(r["notional"]), 1))
                 for _, r in fills[fills["status"] == "FILLED"].iterrows()}
        if key_t != key_l:
            miss_t, miss_l = set(key_l) - set(key_t), set(key_t) - set(key_l)
            bad_c3 = [{"in_ledger_not_trades": sorted(map(str, list(miss_t)[:5])),
                       "in_trades_not_ledger": sorted(map(str, list(miss_l)[:5]))}]
    out["checks"] = {
        "C1_cash_replay_vs_nav_curve": {"ok": not bad_c1, "mismatch_days": bad_c1[:5], "n": len(bad_c1)},
        "C2_nav_composition_cash_plus_mv": {"ok": not bad_c2, "mismatch_days": bad_c2[:5], "n": len(bad_c2)},
        "C3_trades_vs_ledger_rowwise": {"ok": not bad_c3, "diff": bad_c3},
        "C4_share_conservation": {"ok": not bad_c4, "mismatch_days": bad_c4[:5], "n": len(bad_c4)},
    }
    out["all_ok"] = all(v.get("ok", True) for v in out["checks"].values())
    return out


def main() -> int:
    runs = [a for a in sys.argv[1:] if not a.startswith("--")]
    basis = "raw" if "--raw" in sys.argv else "adj"
    results = [check(os.path.join(C.OUTPUT_DIR, r), basis) for r in (runs or ["shadow_run", "long_backtest"])]
    op = os.path.join(C.OUTPUT_DIR, "independent", f"ledger_selfcheck_{basis}.json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump({"_note": "C1现金流水/C2 NAV构成/C3明细对齐/C4股数守恒; 估值域=" + basis,
                   "results": results}, f, ensure_ascii=False, indent=1)
    print(json.dumps(results, ensure_ascii=False, indent=1)[:2500])
    return 0 if all(r.get("all_ok", True) for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
