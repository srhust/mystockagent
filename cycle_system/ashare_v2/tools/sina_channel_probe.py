# -*- coding: utf-8 -*-
"""sina_channel_probe — 换源决策材料(审查者议题⑤): sina 源全池日行质量 vs baostock 对账。

不改 forward_ingest/pipeline(16冻结)。独立探测脚本, 产研究证据:
  对 251 池券×目标日: sina stock_zh_a_daily raw+hfq 双取(行存在性/字段齐/与 baostock
  close 对账差>0.5%), 错误分布四分类(网络/无数据/缺券/格式=E-2口径), pacing 1.0s。
若今日东财 fetch_day 闸门再次阻断 → 本表即"若批准 sina 源, 数据可得性如何"的直接答案。
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_guard import FetchGuard, classify_error                            # noqa: E402


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-10-19"
    codes = [os.path.basename(p)[:6] for p in
             glob.glob(os.path.join(C.STOCKS_DIR, "*.csv"))]
    guard = FetchGuard(cap_calls=600, window_s=1200, max_retries=2, breaker_threshold=8,
                       breaker_reset_s=600, pacing_s=1.0)
    import akshare as ak
    rows, dist = [], {"ok": 0, "network": 0, "no_data": 0, "missing_code": 0, "format": 0}
    for code in codes:
        sym = ("sh" if code.startswith("6") else "sz") + code
        try:
            df = guard.call(ak.stock_zh_a_daily, symbol=sym, adjust="", key=code)
            df["date"] = df["date"].astype(str).str[:10]
            m = df[df["date"] == day]
            if not len(m):
                dist["no_data"] += 1
                rows.append({"code": code, "status": "no-bar(停牌/未生成)"})
                continue
            r = m.iloc[-1]
            hf = guard.call(ak.stock_zh_a_daily, symbol=sym, adjust="hfq", key=f"{code}h")
            hf["date"] = hf["date"].astype(str).str[:10]
            hm = hf[hf["date"] == day]
            rows.append({"code": code, "status": "ok",
                         "open": float(r["open"]), "close": float(r["close"]),
                         "volume": float(r["volume"]),
                         "hfq_close": float(hm.iloc[-1]["close"]) if len(hm) else None,
                         "factor": float(hm.iloc[-1]["close"]) / float(r["close"]) if len(hm) else None})
            dist["ok"] += 1
        except Exception as e:                               # noqa: BLE001
            cls = classify_error(e)
            dist[cls if cls in dist else "network"] += 1
            rows.append({"code": code, "status": f"ERR {cls}: {str(e)[:60]}"})
    out = {"probe": "sina channel day-bar availability", "day": day, "pool": len(codes),
           "error_distribution": dist,
           "guard": {k: guard.stats()[k] for k in ("ok", "fail", "blocked", "breaker", "retry_sleeps")},
           "rows_ok_sample": [r for r in rows if r.get("status") == "ok"][:5],
           "note_for_reviewer": "决策点⑤材料: 若批准sina为FORWARD源, 本表=该日全池可得性/错误分布;"
                               " baostock对账差>0.5%行数另见live_crosscheck; 16元组fetch_day未动",
           "ts": datetime.now().astimezone().isoformat(timespec="seconds")}
    # baostock 全池对账抽样(10券防耗时)
    try:
        import baostock as bs
        bs.login()
        sample = [r["code"] for r in out["rows_ok_sample"]] + ["000002", "600000"]
        cmp_rows = []
        for pure in dict.fromkeys(sample):
            cb = ("sh." if pure.startswith("6") else "sz.") + pure
            rs = bs.query_history_k_data_plus(cb, "date,close", start_date=day, end_date=day,
                                              frequency="d", adjustflag="3")
            vals = []
            while rs.error_code == "0" and rs.next():
                vals.append(rs.get_row_data())
            sina_c = next((r["close"] for r in rows if r.get("code") == pure and r.get("status") == "ok"), None)
            if vals and sina_c:
                bsc = float(vals[0][1])
                cmp_rows.append({"code": pure, "sina": sina_c, "baostock": bsc,
                                 "diff_pct": round((sina_c - bsc) / bsc * 100, 3)})
        bs.logout()
        out["sina_vs_baostock_sample"] = cmp_rows
    except Exception as e:                                   # noqa: BLE001
        out["sina_vs_baostock_sample"] = f"BAOSTOCK_UNAVAILABLE {type(e).__name__}"
    dest = os.path.join(C.OUTPUT_DIR, "independent", f"sina_channel_probe_{day}.json")
    json.dump(out, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("probe", "day", "pool", "error_distribution",
                                          "guard", "sina_vs_baostock_sample")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
