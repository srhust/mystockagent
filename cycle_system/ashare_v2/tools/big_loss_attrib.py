# -*- coding: utf-8 -*-
"""big_loss_attrib — 阶段2附属: 最大亏损交易独立归因(审查第三轮指令点名601069/002207)。

对81日SHADOW(旧close_adj成交域)与多年REPLAY的每笔FIFO平仓配对, 提取:
买入信号证据(opportunities当日engines/cycle_stage/pts/cred/trust) · 持仓期MAE/MFE
(close_adj域逐日, 同时给出raw域对照以显式化旧口径失真) · 卖出信号日与卖出价 · 占亏损比。
读数全部标注REPLAY/旧口径不可作性能结论; RAW重跑后此工具自动兼容新账本。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                              # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                   # noqa: E402

TARGETS = {"601069", "002207"}


def _pair(trades: pd.DataFrame):
    q = defaultdict(list)
    rows = []
    for _, r in trades[trades.action == "BUY"].sort_values("exec_day").iterrows():
        q[str(r.code)].append({"day": r.exec_day, "sh": int(r.shares),
                               "cost": float(r.notional) + float(r.fees), "px": float(r.price),
                               "signal_day": r.signal_day})
    for _, r in trades[trades.action == "SELL"].sort_values("exec_day").iterrows():
        need, cost, buyinfo = int(r.shares), 0.0, []
        while need > 0 and q[str(r.code)]:
            e = q[str(r.code)].pop(0)
            take = min(e["sh"], need)
            cost += e["cost"] * take / e["sh"]
            need -= take
            buyinfo.append(e)
        if not buyinfo:
            continue
        pnl = float(r.notional) - float(r.fees) - cost
        rows.append({"code": str(r.code), "buy_signal_day": buyinfo[0]["signal_day"],
                     "buy_exec_day": buyinfo[0]["day"], "buy_px": buyinfo[0]["px"],
                     "sell_exec_day": r.exec_day, "sell_signal_day": r.signal_day,
                     "sell_px": float(r.price), "shares": int(r.shares),
                     "pnl": round(pnl, 2), "ret": round(pnl / cost, 4) if cost else 0.0,
                     "cost": round(cost, 2)})
    return rows


def _mae_mfe(panel: pd.DataFrame, code: str, d0: str, d1: str, buy_px: float) -> dict:
    g = panel[(panel["code"] == code) & (panel["date"] >= pd.Timestamp(d0)) & (panel["date"] <= pd.Timestamp(d1))]
    if not len(g):
        return {}
    adj = g["close_adj"].astype(float)
    raw = g["close"].astype(float)
    base_adj, base_raw = float(adj.iloc[0]), float(raw.iloc[0])
    return {"mae_adj": round(float(adj.min() / base_adj - 1), 4), "mfe_adj": round(float(adj.max() / base_adj - 1), 4),
            "mae_raw": round(float(raw.min() / base_raw - 1), 4), "mfe_raw": round(float(raw.max() / base_raw - 1), 4),
            "days_held": int(len(g))}


def main() -> int:
    portal = DataPortal(data_mode=C.PIT_RESEARCH)
    panel = portal.price_panel()
    out = {"_disclaimer": "REPLAY旧口径(close_adj成交)归因: 用于审查点名交易的行为分析, 非性能结论; "
                          "RAW域重跑后将出新版对照。", "runs": {}}
    for run in ("shadow_run", "long_backtest"):
        tp = os.path.join(C.OUTPUT_DIR, run, "trades.csv")
        if not os.path.exists(tp):
            continue
        t = pd.read_csv(tp, encoding="utf-8-sig", dtype={"code": str})
        t["code"] = t["code"].str.zfill(6)
        pairs = _pair(t)
        tot_loss = sum(p["pnl"] for p in pairs if p["pnl"] < 0)
        rows = []
        for p in sorted(pairs, key=lambda x: x["pnl"])[:6] + [q for q in pairs if q["code"] in TARGETS and q not in sorted(pairs, key=lambda x: x["pnl"])[:6]]:
            ev = {}
            for run2 in (run,):
                op = pd.read_csv(os.path.join(C.OUTPUT_DIR, run2, "opportunities.csv"), encoding="utf-8-sig", dtype={"code": str})
                m = op[(op["code"] == p["code"]) & (op["day"] == p["buy_signal_day"])]
                if len(m):
                    r0 = m.iloc[0]
                    ev = {"engines": r0.get("engines"), "cycle_stage": r0.get("cycle_stage"),
                          "entry_kinds": r0.get("entry_kinds"), "score_points": float(r0.get("score_points", 0)),
                          "cred": r0.get("data_credibility"), "trust": r0.get("membership_trust")}
            ind = panel[(panel["code"] == p["code"])].drop_duplicates("sector")["sector"]
            rows.append({**p, "sector": (ind.iloc[0] if len(ind) else None),
                         "buy_evidence": ev,
                         "holding_mae_mfe": _mae_mfe(panel, p["code"], p["buy_exec_day"], p["sell_exec_day"], p["buy_px"]),
                         "share_of_total_loss": round(p["pnl"] / tot_loss, 4) if tot_loss else None})
        out["runs"][run] = {"closed_pairs": len(pairs), "total_realized_loss": round(tot_loss, 2),
                            "worst_and_targeted": rows}
    op = os.path.join(C.OUTPUT_DIR, "independent", "big_loss_attribution.json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:3500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
