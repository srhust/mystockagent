# -*- coding: utf-8 -*-
"""poc2000_smoke — 2000扩展 POC M1: 最小冒烟(≤50请求预算, baostock独立通道)。

裁定纪律: POC先行严禁全量; 本脚本只做三件事——
  1) baostock login 连通性(1次)
  2) 分层样本 3 券 × 极短窗日线(600519 2000-01 存续 / 000001 2000-01 存续 / 退市券 2005)
     含 tradestatus/isST 字段存在性核验
  3) 落 data/ashare_history_2000/daily_raw/ + manifest(行数/sha256/错误分布/guard.stats)
不通过(缺字段/零行/错误不可解释) → 保持 M1 状态, 不得进 M2。
全程经 FetchGuard(cap/退避/熔断), 请求预算硬顶 50。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                  # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                             # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_guard import FetchGuard                                          # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
BUDGET = 50
SAMPLES = [  # (baostock code, 标签, start, end) — 分层小样本, M2才扩12券×6切片
    ("sh.600000", "survivor_pre2000_sh", "2000-01-03", "2000-01-31"),
    ("sz.000001", "survivor_pre2000_sz", "2000-01-03", "2000-01-31"),
    ("sh.600888", "delisted_window_2005", "2005-01-03", "2005-04-30"),   # 退市券历史在窗
]
REQUIRED_COLS = {"date", "open", "high", "low", "close", "preclose", "volume",
                 "amount", "turn", "tradestatus", "isST"}


def bs_query_daily(bs, code, start, end):
    rs = bs.query_history_k_data_plus(code, ",".join(sorted(REQUIRED_COLS)),
                                      start_date=start, end_date=end, frequency="d", adjustflag="3")
    import pandas as pd
    frames = []
    while rs.error_code == "0" and rs.next():
        frames.append(rs.get_row_data())
    df = pd.DataFrame(frames, columns=rs.fields)
    return df


def main() -> int:
    os.makedirs(os.path.join(ROOT, "daily_raw"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "manifest"), exist_ok=True)
    guard = FetchGuard(cap_calls=BUDGET, window_s=600, max_retries=2, breaker_threshold=4,
                       breaker_reset_s=60)
    try:
        import baostock as bs
    except ImportError:
        print(json.dumps({"M1": "BAOSTOCK_INSTALL_REQUIRED"}))
        return 2
    import pandas as pd

    res = bs.login()
    login_ok = res.error_code == "0"
    report = {"milestone": "M1_smoke", "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
              "baostock_login": {"error_code": res.error_code, "msg": res.error_msg},
              "budget": BUDGET, "queries": [], "verdict": None}
    if not login_ok:
        report["verdict"] = "M1_FAIL_LOGIN"
        _write(report, guard)
        bs.logout()
        return 1
    all_ok = True
    for code, tag, s, e in SAMPLES:
        entry = {"code": code, "tag": tag, "window": [s, e]}
        try:
            df = guard.call(bs_query_daily, bs, code, s, e, key=code)
            entry.update({"rows": len(df), "cols_present": sorted(set(df.columns)),
                          "missing_cols": sorted(REQUIRED_COLS - set(df.columns)),
                          "tradestatus_vals": sorted(df["tradestatus"].unique())[:4] if "tradestatus" in df else None,
                          "isST_vals": sorted(df["isST"].unique())[:4] if len(df) and "isST" in df else None})
            if len(df) and not entry["missing_cols"]:
                h = hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()[:16]
                dest = os.path.join(ROOT, "daily_raw", f"{code}_{s}_{e}.csv")
                df.to_csv(dest, index=False, encoding="utf-8-sig")
                entry.update({"sha256_16": h, "file": os.path.basename(dest)})
                # 日期单调 + 非全停牌(退市券允许尾部停)
                entry["date_monotonic"] = bool(df["date"].is_monotonic_increasing)
            else:
                all_ok = False
        except Exception as ex:                                # noqa: BLE001 冒烟: 全收全记
            entry["error"] = f"{type(ex).__name__}: {ex}"[:160]
            all_ok = False
        report["queries"].append(entry)
    bs.logout()
    report["guard_stats"] = guard.stats()
    report["used_budget_note"] = f"calls={len(guard.calls)}/{BUDGET}"
    report["verdict"] = ("M1_PASS_PENDING_M2_REVIEW" if all_ok else "M1_FAIL_SEE_QUERIES")
    _write(report, guard)
    print(json.dumps(report, ensure_ascii=False, indent=1)[:2600])
    return 0 if all_ok else 1


def _write(report, guard):
    report["guard_stats_at_write"] = guard.stats()
    dest = os.path.join(ROOT, "manifest", "m1_smoke.json")
    json.dump(report, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    sys.exit(main())
