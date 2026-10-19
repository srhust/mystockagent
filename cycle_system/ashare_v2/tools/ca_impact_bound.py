# -*- coding: utf-8 -*-
"""ca_impact_bound — 包D集成前定量: 企业行动对现有R1重放NAV的影响边界(只读, 不产正式产物)。

输入: r1_bt nav_hist_full(逐日pos/cash) + trade_audit(fills) + 面板raw close + 事件库591条。
测量(全部为**边界**, 供ca_bt正式集成立项验收口径, 不改任何既有产物):
  1. DIVIDEND 现金漏记: 逐日持仓total×每股税后(下界=min options, 上界=max options) 累计入账
     → 漏计金额占终值NAV比例; 税档歧义带宽=上界-下界
  2. BONUS 除权虚损: ex_date当日raw跌幅×持仓市值(股数未调→虚假亏损) 累计
     (校验: 同日adj_factor比率应≈1/(1+ratio)——面板因子自洽旁证)
  3. 两效应合并对dd触锁时点的敏感带(粗查: LOCKOUT日期±NAV差)
框架: 影响测量, 非绩效; R1/R0产物零触碰。
"""
from __future__ import annotations

import glob
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                # noqa: E402

R1 = os.path.join(C.OUTPUT_DIR, "r1_bt", "r1_2020-06-01_2026-10-16")
CA = os.path.join(C.DATA_DIR, "ashare_history_2000", "corporate_actions", "full_replay_pool")


def main() -> int:
    nav = pd.read_csv(os.path.join(R1, "nav_hist_full.csv"))
    days = list(nav["day"])
    # 持仓时间线: nav_hist无positions列(flatten), 从trade_audit fills重建 per-code 累计股数
    ta = pd.read_csv(os.path.join(R1, "trade_audit.csv"), dtype={"code": str})
    fills = ta[ta["action"].isin(["BUY", "SELL"])].copy()
    fills["signed"] = fills["shares"].astype(float) * fills["action"].map({"BUY": 1.0, "SELL": -1.0})
    cum = {}
    for code, g in fills.groupby("code"):
        g = g.sort_values("exec_day")
        cum[code] = (list(g["exec_day"]), g["signed"].cumsum().tolist())

    def held_at_or_before(code, day):
        seq = cum.get(code)
        if not seq:
            return 0.0
        ds, vs = seq
        import bisect
        i = bisect.bisect_right(ds, day) - 1
        return vs[i] if i >= 0 else 0.0
    events = []
    for f in glob.glob(os.path.join(CA, "*.events.json")):
        events += json.load(open(f, encoding="utf-8"))["events"]
    div_lo = div_hi = bonus = 0.0
    n_div = n_bonus = 0
    per_code_raw = {}

    def raw_series(code):
        if code not in per_code_raw:
            p = os.path.join(C.STOCKS_DIR, f"{code}.csv")
            if not os.path.exists(p):
                per_code_raw[code] = None
            else:
                df = pd.read_csv(p, dtype={"date": str})
                df["date"] = df["date"].str[:10]
                per_code_raw[code] = df.set_index("date")
        return per_code_raw[code]

    for e in events:
        code, exd = e["code"], e["ex_date"]
        if exd not in days:
            continue                                   # 非交易日/窗外(面板日期为交易日)
        held = None
        # 用除权日**前一交易日**持仓(股权登记日语义: 前日成交累计)
        i = days.index(exd)
        if i == 0:
            continue
        prev = days[i - 1]
        held = held_at_or_before(code, prev)
        if held <= 0:
            continue
        df = raw_series(code)
        if e["type"] == "DIVIDEND":
            opts = e.get("cash_after_tax_options")
            if opts:
                lo, hi = min(opts), max(opts)
            else:
                tax = e.get("tax_rate", 0.2)
                lo = hi = e["cash_per_share"] * (1 - tax)
            div_lo += held * lo; div_hi += held * hi; n_div += 1
        elif e["type"] == "BONUS" and df is not None and prev in df.index and exd in df.index:
            c0 = float(df.at[prev, "close"]); c1 = float(df.at[exd, "close"])
            if c0 > 0:
                bonus += held * c0 * (1 - c1 / c0)     # 股数未调→虚损上界(跌幅全算亏损)
                n_bonus += 1
    final_nav = float(nav["nav"].iloc[-1])
    out = {
        "frame": "包D集成影响边界测量(只读, 非绩效, R0/R1零触碰)",
        "events_used": {"DIVIDEND_hits": n_div, "BONUS_hits": n_bonus, "events_total": len(events)},
        "dividend_cash_omitted": {"lower_tax": round(div_lo, 2), "upper_taxfree": round(div_hi, 2),
                                  "as_pct_of_final_nav": [round(div_lo / final_nav * 100, 2),
                                                           round(div_hi / final_nav * 100, 2)]},
        "bonus_phantom_loss_upper": round(bonus, 2),
        "bonus_phantom_as_pct_nav": round(bonus / final_nav * 100, 2),
        "caveat": "边界用除权前日持仓×全额, 未扣同期卖出/费用; BONUS虚损未与adj_factor旁证逐事件核",
        "decision_input": "若phantom+dividend合计占NAV>5%, ca_bt全事件overlay集成列为下一工作包必做; "
                          "验收=ledger_selfcheck C1-C4全过+守恒含企业行动项"}
    dest = os.path.join(C.OUTPUT_DIR, "independent", "ca_impact_bound.json")
    json.dump(out, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
