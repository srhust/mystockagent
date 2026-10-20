# -*- coding: utf-8 -*-
"""poc2000_m4_batch — M4 全量扩量分批器(**未获M3批准前禁止运行**, 本文件仅为预备).

设计(HISTORY_2000_POC_PLAN.md §M4 + M3_REVIEW_PACKAGE 议题3):
  按年切片(2000..2019)分批, 每批=全市场主表当日券×单年日线(tradestatus/isST齐),
  断点续传(已有且行数>0的 {code}_{year}.csv 跳过), QC门(每批后行数分布/字段完整/
  与trade_dates对账), fetch_guard pacing 0.35s + 熔断退避, 单批可中断。
数据层纪律: 全部落 data/ashare_history_2000/daily/{year}/; 行业PIT保持UNKNOWN标注。
红线: 本脚本**不得**触碰 cycle_system/ashare_v2 预测16域/R0/R1产物/ca_bt基线。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                        # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                   # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_guard import FetchGuard, classify_error                               # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
COLS = "date,open,high,low,close,preclose,volume,amount,turn,tradestatus,isST"


def master_for_year(bs, year: int) -> list:
    """取该年年末最后交易日主表(快照口径, 与M2一致)。M3批准后实装(query_all_stock)。"""
    raise RuntimeError("M4未批准——本脚本禁止运行")


def pull_year(bs, year: int, codes: list, guard: FetchGuard) -> dict:
    out = os.path.join(ROOT, "daily", str(year))
    os.makedirs(out, exist_ok=True)
    stats = {"ok": 0, "skip": 0, "fail": 0, "rows": []}
    for code in codes:
        dest = os.path.join(out, f"{code}.csv")
        if os.path.exists(dest) and sum(1 for _ in open(dest, encoding="utf-8")) > 1:
            stats["skip"] += 1
            continue

        def _q(c=code):
            return bs.query_history_k_data_plus(c, COLS, start_date=f"{year}-01-01",
                                                end_date=f"{year}-12-31",
                                                frequency="d", adjustflag="3")
        try:
            rs = guard.call(_q)
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                stats["skip"] += 1
                continue
            with open(dest, "w", encoding="utf-8") as f:
                f.write(COLS + "\n")
                for r in rows:
                    f.write(",".join(r) + "\n")
            stats["ok"] += 1
            stats["rows"].append(len(rows))
        except Exception as e:  # noqa: BLE001
            stats["fail"] += 1
            stats.setdefault("errors", []).append(f"{code}: {classify_error(e)}")
    return stats


def qc_gate(year: int, stats: dict) -> dict:
    import numpy as np
    arr = np.array(stats["rows"]) if stats["rows"] else np.array([0])
    return {"year": year, "ok": stats["ok"], "skip": stats["skip"], "fail": stats["fail"],
            "rows_p50": int(np.median(arr)), "rows_max": int(arr.max()),
            "empty_file_check": sum(1 for fn in os.listdir(os.path.join(ROOT, "daily", str(year)))
                                    if os.path.getsize(os.path.join(ROOT, "daily", str(year), fn)) <= 40)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approve-m3", required=True,
                    help="必须显式携带审查者批准标记(填批准记录文件路径), 否则拒绝运行")
    ap.add_argument("--years", default="", help="逗号分隔年份子集, 空=2000..2019全部")
    a = ap.parse_args()
    if not os.path.exists(a.approve_m3):
        print("M3批准记录不存在——按'POC先行严禁全量'纪律拒绝运行。")
        return 9
    import baostock as bs
    years = [int(y) for y in a.years.split(",")] if a.years else list(range(2000, 2020))
    bs.login()
    guard = FetchGuard(cap_calls=3000, window_s=3600, max_retries=3, breaker_threshold=10,
                       breaker_reset_s=900, pacing_s=0.35)
    report = []
    for y in years:
        codes = master_for_year(bs, y)          # 见占位: 批准后以年末主表实装
        st = pull_year(bs, y, codes, guard)
        qc = qc_gate(y, st)
        report.append(qc)
        json.dump(report, open(os.path.join(ROOT, "QC", "m4_batch_report.json"), "w",
                               encoding="utf-8"), ensure_ascii=False, indent=1)
        print(y, qc, flush=True)
    bs.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
