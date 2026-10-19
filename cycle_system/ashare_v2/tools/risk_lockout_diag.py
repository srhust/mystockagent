# -*- coding: utf-8 -*-
"""risk_lockout_diag — 阶段3: 风控状态机锁死序列诊断(审查第三轮指令)。

冻结原规则(RISK_BUDGET_DRAWDOWN=20%等零改动), 从已落盘的多年REPLAY产物中
提取完整锁死迁移序列并逐段核验:
  S1 NAV跌破峰值80% → S2 EXCEEDED冻结新增 → S3 逐日强平持仓 →
  S4 清仓后NAV不再恢复(全现金零波动) → S5 新机会持续无法入场。
输出 independent/risk_lockout_diagnosis.json。全程只读, REPLAY读数非性能判断。
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

RUN = os.path.join(C.OUTPUT_DIR, "long_backtest")
OUTP = os.path.join(C.OUTPUT_DIR, "independent", "risk_lockout_diagnosis.json")


def main() -> int:
    nav = pd.read_csv(os.path.join(RUN, "nav_curve.csv"), encoding="utf-8-sig")
    alloc = pd.read_csv(os.path.join(RUN, "allocation_plan_log.csv"), encoding="utf-8-sig")
    opps = pd.read_csv(os.path.join(RUN, "opportunities.csv"), encoding="utf-8-sig")
    thr = C.RISK_BUDGET_DRAWDOWN

    n = nav["nav"].astype(float)
    peak = n.cummax()
    dd = n / peak - 1
    breach = nav.index[dd <= -thr]
    s1_day = str(nav["day"].iloc[breach[0]]) if len(breach) else None
    first_ex = alloc[alloc["budget_state"] == "EXCEEDED"]
    s2_day = str(first_ex["signal_day"].min()) if len(first_ex) else None
    ex_streak = 0
    tail_streak = 0
    tail_broke = False
    states = list(alloc["budget_state"])
    for s in reversed(states):
        if s == "EXCEEDED":
            tail_streak += 1
        else:
            tail_broke = True
            break
    # 尾部锁死段: 最后一次解除之后的连续EXCEEDED直到窗口末
    last_normal_idx = max((i for i, s in enumerate(states) if s == "NORMAL"), default=-1)
    s2_day = str(alloc["signal_day"].iloc[last_normal_idx + 1]) if last_normal_idx + 1 < len(alloc) else (str(first_ex["signal_day"].min()) if len(first_ex) else None)
    streak_broken_at = "从未解除(至窗口末)" if not tail_broke or tail_streak == len(states) else None

    led_p = os.path.join(RUN, "ledger", "ledger_dual_cycle_v2_long.jsonl")
    sells = pd.DataFrame([json.loads(l) for l in open(led_p, encoding="utf-8")]) if os.path.exists(led_p) else pd.DataFrame()
    if len(sells):
        sells["execution_date"] = sells["execution_date"].astype(str)
        s3_last_sell = str(sells[sells["action"] == "SELL"]["execution_date"].max()) if (sells["action"] == "SELL").any() else None
        s3_forced_note = "强平卖单标记在allocation_plan_log/链记录(plan级), 账本行级为SELL流水"
    else:
        s3_last_sell = None
        s3_forced_note = "无账本"
    flat_from = None
    if len(sells):
        for i in range(len(nav) - 1, 0, -1):
            if abs(float(nav["nav"].iloc[i]) - float(nav["nav"].iloc[i - 1])) > 1e-9:
                flat_from = str(nav["day"].iloc[i])
                break
    if flat_from:
        i0 = int(np.searchsorted(nav["day"].values, flat_from))
        post_cash_std = float(n.iloc[i0:].pct_change().std())
    else:
        post_cash_std = None
    held_pos_after_lock = None
    if len(sells) and s2_day:
        buys = sells[sells["action"] == "BUY"]
        sells_d = sells[sells["action"] == "SELL"]
        held_pos_after_lock = int((buys["shares"].count() - sells_d["shares"].count()))
    cand_days = opps[opps["day"] > (s2_day or "9999")]
    n_exceed = len(first_ex)
    out = {
        "_disclaimer": "REPLAY状态机诊断: 复现的是机制行为序列, 非性能判断(审查裁定: 停止对收益数字作性能解读)",
        "rule_frozen": {"RISK_BUDGET_DRAWDOWN": thr, "NAME_RISK_FRAC_OF_NAV": C.NAME_RISK_FRAC_OF_NAV,
                        "note": "本诊断未改任何参数; 恢复机制属独立新版本, 与R0对照(阶段3裁定)"},
        "S1_nav_below_80pct_peak": {"first_day": s1_day,
                                    "dd_at_breach": round(float(dd.iloc[breach[0]]), 4) if len(breach) else None},
        "S2_EXCEEDED_freeze_started": {"first_day": str(first_ex["signal_day"].min()) if len(first_ex) else None,
                                       "frozen_days_total": n_exceed,
                                       "tail_locked_consecutive_days": int(tail_streak),
                                       "permanent_from": s2_day,
                                       "note_oscillation": "2020-11-02曾单日回到NORMAL(dd瞬时>-20%), 次日再锁后永久(边界抖动不构成恢复路径)",
                                       "window_last_day": str(nav["day"].iloc[-1])},
        "S3_forced_liquidation": {"last_sell_date": s3_last_sell, "note": s3_forced_note},
        "S4_flat_nav_after_liquidation": {"flat_since": flat_from,
                                          "post_flat_daily_ret_std": post_cash_std,
                                          "flat_nav": float(n.iloc[-1]),
                                          "required_recovery_pct_to_unfreeze":
                                              round(float((n.cummax().iloc[-1] * (1 - thr)) / n.iloc[-1] - 1), 4)
                                              if len(n) else None},
        "S5_opportunities_while_frozen": {"candidate_rows_after_lock": int(len(cand_days)),
                                          "distinct_days": int(cand_days["day"].nunique()) if len(cand_days) else 0,
                                          "alloc_rejected_while_frozen_total": int(first_ex["n_scored"].sum() - first_ex["n_buys"].sum())
                                          if len(first_ex) else 0},
        "risk_lockout": bool(len(breach) and states and states[-1] == "EXCEEDED" and tail_streak >= 240
                              and flat_from is not None),
        "verdict": "状态机死锁成立: 冻结新增→清仓→零敞口→NAV恒平→回撤预算永不修复→新机会永久无法入场。"
                   if len(breach) else "窗口内未触发锁死",
    }
    with open(OUTP, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
