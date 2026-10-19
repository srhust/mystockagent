# -*- coding: utf-8 -*-
"""raw_old_pairing — 阶段1-4: 81日窗新旧逐笔对照 + NAV差两段分解。

旧基线 = shadow_run/trades.csv(末次覆盖运行, adj域; 其jsonl账本污染但csv与nav_curve同run)。
新账本 = raw_bt/81日/trade_audit.csv(status FILLED) + rejects。
按(signal_day, code, action)配对, 逐笔列 成交价/股数/名义额/cash_delta 差异;
NAV差分解: A 同决策笔的口径差(截至两run同点) + B 路径截断差(新03-24锁死清仓 vs
旧继续交易至06-16的后续盈亏) —— 归因表逐项可复算。
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                              # noqa: E402

OLD_T = os.path.join(C.OUTPUT_DIR, "shadow_run", "trades.csv")
NEW_A = os.path.join(C.OUTPUT_DIR, "raw_bt", "2026-03-02_2026-06-30", "trade_audit.csv")
OLD_N = os.path.join(C.OUTPUT_DIR, "shadow_run", "nav_curve.csv")
NEW_N = os.path.join(C.OUTPUT_DIR, "raw_bt", "2026-03-02_2026-06-30", "nav_hist_full.csv")


def main() -> int:
    old = pd.read_csv(OLD_T, encoding="utf-8-sig", dtype={"code": str})
    old = old[old["action"].isin(["BUY", "SELL"])].copy()
    old["code"] = old["code"].str.zfill(6)
    new = pd.read_csv(NEW_A, encoding="utf-8-sig", dtype={"code": str})
    newf = new[new["action"].isin(["BUY", "SELL"])].copy()
    key = lambda df: list(zip(df["signal_day"], df["code"], df["action"]))
    old["k"], newf["k"] = key(old), key(newf)
    mo = old.set_index("k")
    rows = []
    for k in sorted(set(old["k"]) | set(newf["k"]), key=lambda x: (str(x[0]), str(x[1]), str(x[2]))):
        o = mo.loc[[k]] if k in set(old["k"]) else None
        nn = newf[newf["k"] == k] if k in set(newf["k"]) else None
        o0 = o.iloc[0] if o is not None and len(o) else None
        n0 = nn.iloc[0] if nn is not None and len(nn) else None
        rows.append({
            "signal_day": k[0], "code": k[1], "action": k[2],
            "present": ("both" if o0 is not None and n0 is not None else ("old_only" if o0 is not None else "new_only")),
            "old_price_close_adj": None if o0 is None else round(float(o0["price"]), 4),
            "new_price_raw_open": None if n0 is None else round(float(n0["exec_raw_open"]), 4),
            "old_shares": None if o0 is None else int(o0["shares"]),
            "new_shares": None if n0 is None else int(n0["shares"]),
            "old_cash_delta": None if o0 is None else (round(-(float(o0["notional"]) + float(o0["fees"])), 2)
                                                       if k[2] == "BUY" else round(float(o0["notional"]) - float(o0["fees"]), 2)),
            "new_cash_delta": None if n0 is None else round(float(n0["cash_delta"]), 2),
            "new_val_raw_close": None if n0 is None else n0["val_raw_close"],
            "plan_hash_new": None if n0 is None else str(n0["plan_hash"]),
        })
    pf = pd.DataFrame(rows)
    op = os.path.join(C.OUTPUT_DIR, "independent", "trade_pairing_81d.csv")
    pf.to_csv(op, index=False, encoding="utf-8-sig")
    rej = new[new["action"].astype(str).str.startswith("REJECT")]
    both = pf[pf["present"] == "both"]
    only_old = int((pf["present"] == "old_only").sum())
    only_new = int((pf["present"] == "new_only").sum())
    on_old = old[old["exec_day"] <= "2026-04-01"]
    on_new = newf[newf["exec_day"].astype(str) <= "2026-04-01"]
    d_cash_old = float(on_old["notional"].mul(on_old["action"].map({"BUY": -1, "SELL": 1})).sum()
                        - on_old["fees"].sum())
    d_cash_new = float(on_new["cash_delta"].astype(float).sum())
    oldnav = pd.read_csv(OLD_N, encoding="utf-8-sig")
    newnav = pd.read_csv(NEW_N, encoding="utf-8-sig")
    o0401 = oldnav[oldnav["day"] <= "2026-04-01"]["nav"].iloc[-1]
    n0401 = newnav[newnav["day"] <= "2026-04-01"]["nav"].iloc[-1]
    tail_old = float(oldnav["nav"].iloc[-1]) - o0401
    out = {
        "_disclaimer": "旧基线为末次覆盖运行trades.csv(adj域, 其jsonl账本污染已取证); 配对按(signal_day,code,action)。"
                       "不作性能判断, 只作口径差分解。",
        "pairs_both": int(len(both)), "old_only": only_old, "new_only": only_new,
        "rejects_new": int(len(rej)),
        "per_pair": rows,
        "nav_diff_decomposition": {
            "old_final_nav": round(float(oldnav["nav"].iloc[-1]), 2),
            "new_final_nav": round(float(newnav["nav"].iloc[-1]), 2),
            "total_diff_pp": round(float((oldnav["nav"].iloc[-1] - newnav["nav"].iloc[-1]) / 20000), 4),
            "B_path_truncation": {
                "note": "新run 03-24 EXCEEDED→04-01清仓后NAV恒平; 旧run 04-01后继续交易",
                "old_nav_0401": round(float(o0401), 2), "new_nav_0401": round(float(n0401), 2),
                "old_post_0401_pnl": round(tail_old, 2),
                "contrib_pp": round(tail_old / 20000, 4)},
            "A_fill_domain_diff_to_0401": {
                "old_net_cash_to_0401": round(d_cash_old, 2), "new_net_cash_to_0401": round(d_cash_new, 2),
                "cash_gap": round(d_cash_old - d_cash_new, 2),
                "nav_gap_0401": round(float(o0401 - n0401), 2),
                "contrib_pp": round(float(o0401 - n0401) / 20000, 4)}},
    }
    with open(os.path.join(C.OUTPUT_DIR, "independent", "trade_pairing_81d.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "per_pair"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
