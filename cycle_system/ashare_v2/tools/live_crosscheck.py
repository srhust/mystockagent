# -*- coding: utf-8 -*-
"""live_crosscheck — 明日直播 baostock 独立对账(不改任何入账路径, 纯证据工具)。

背景(M2实证): sina 面板存在当日缓存滞后(000002@10-16 差1.61%, baostock为真值)。
FORWARD 账本走 16 元组 forward_ingest(sina) 原路径**不变**(红线), 本工具在 plan/execute
后用 baostock 独立通道拉当日 OHLCV, 与链/账本中的价格逐券对比, 产出对账证据:
  - 偏差>0.5% 的券逐行列示(CORP侧发现 sina 缓存滞后 → 记更正, 不回填不改写)
  - baostock 缺当日报 TRADE_DATE_NOT_IN_BAOSTOCK(Y+1 更新延迟, 非错误)
用法: python -X utf8 tools/live_crosscheck.py 2026-10-20 [--codes 600000,000001]
退出码: 0=对账完成(含发现差异), 1=baostock通道不可用(证据登记后仍0副作用)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_guard import FetchGuard                                            # noqa: E402

FLD = "date,open,high,low,close,tradestatus"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day")
    ap.add_argument("--codes", default="600000,000001,000002,600036")
    a = ap.parse_args()
    guard = FetchGuard(cap_calls=50, window_s=120, max_retries=2, breaker_threshold=4,
                       breaker_reset_s=300, pacing_s=0.3)
    out = {"tool": "live_crosscheck", "day": a.day,
           "frame": "独立对账证据(FORWARD入账路径=16元组sina不动, 红线)", "rows": [],
           "verdict": None}
    try:
        import baostock as bs
        bs.login()
    except Exception as e:                                     # noqa: BLE001
        out["verdict"] = f"BAOSTOCK_UNAVAILABLE {type(e).__name__}"
        _write(out)
        print(json.dumps(out, ensure_ascii=False))
        return 1
    for pure in a.codes.split(","):
        pure = str(pure).strip().zfill(6)   # 防御: 某些shell/JSON链路会把"000002"变数字2
        code = ("sh." if pure.startswith("6") else "sz.") + pure
        try:
            def _q(c=code):
                rs = bs.query_history_k_data_plus(c, FLD, start_date=a.day, end_date=a.day,
                                                  frequency="d", adjustflag="3")
                if rs.error_code != "0":
                    raise RuntimeError(rs.error_msg)
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                return rows
            rows = guard.call(_q, key=code)
            if not rows:
                out["rows"].append({"code": pure, "bs": None,
                                    "note": "TRADE_DATE_NOT_IN_BAOSTOCK(更新延迟非错误)"})
                continue
            d = dict(zip("date open high low close tradestatus".split(), rows[0]))
            rec = {"code": pure, "bs_open": float(d["open"]), "bs_close": float(d["close"]),
                   "tradestatus": d["tradestatus"]}
            # 与 sina panel(现役 csv)对比
            csv = os.path.join(C.STOCKS_DIR, f"{pure}.csv")
            if os.path.exists(csv):
                sp = pd.read_csv(csv, dtype=str)
                sp["date"] = sp["date"].str[:10]
                m = sp[sp["date"] == a.day]
                if len(m):
                    sc = float(m["close"].iloc[-1])
                    rec["sina_close"] = sc
                    rec["diff_pct"] = round((sc - rec["bs_close"]) / rec["bs_close"] * 100, 3)
                    rec["flag"] = "SINA_STALE_SUSPECTED" if abs(rec["diff_pct"]) > 0.5 else "MATCH"
                else:
                    rec["sina_close"] = None
                    rec["flag"] = "SINA_MISSING_TODAY"
            out["rows"].append(rec)
        except Exception as e:                                 # noqa: BLE001
            out["rows"].append({"code": pure, "error": f"{type(e).__name__}: {str(e)[:90]}"})
    bs.logout()
    out["guard"] = guard.stats()
    flags = [r.get("flag") for r in out["rows"] if r.get("flag")]
    out["verdict"] = ("CROSSCHECK_DONE" if flags or out["rows"] else "NO_DATA")
    if any(f == "SINA_STALE_SUSPECTED" for f in flags):
        out["action_required"] = "对差异券登记CORRECTIONS(#append), FORWARD账本不回填不改写(裁定E)"
    _write(out)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def _write(out):
    dest = os.path.join(C.OUTPUT_DIR, "independent", f"crosscheck_{out['day']}.json")
    out["ts"] = datetime.now().astimezone().isoformat(timespec="seconds")
    json.dump(out, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote", dest)


if __name__ == "__main__":
    sys.exit(main())
