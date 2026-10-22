# -*- coding: utf-8 -*-
"""ca_full_ingest_v2 — M4 CA 子批: 全历史池 × 1998-2019 事件摄取(包D的扩量版).

复用 events_for_year 语义(DIVIDEND/BONUS/差别化税), 但:
  - codes = universe/{Y}/alive.csv 并集 1998-2019 (A1官方池, 非涉事156)
  - years = 1998..2019 (每券只查其 ipo..out 交集年, A1区间语义; 省请求)
  - 输出 corporate_actions/ev9819/{code}.events.json (断点续跑跳过)
  - guard: pacing 0.35s, cap 250/60s (保守, baostock 白天窗) + CircuitOpen/RateLimit
    等待重试同券(批3修复语义: 券不消耗, max 6 waits → flush rc7)
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                        # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                   # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_guard import FetchGuard, RateLimitExceeded, CircuitOpen               # noqa: E402
from ca_full_ingest import events_for_year                                       # noqa: E402

OUT = os.path.join(C.DATA_DIR, "ashare_history_2000", "corporate_actions", "ev9819")
YEARS = [str(y) for y in range(1998, 2020)]
RELOGIN_EVERY = 400   # 服务端接收异常=会话陈旧, 主动轮换(13:2x 2094券零年错误实证)


def _bs_relogin(bs):
    try:
        bs.logout()
    except Exception:
        pass
    return bs.login().error_code == "0"


def load_pool():
    """code -> set(years) 按 A1 alive 区间."""
    pool = {}
    for p in sorted(glob.glob(os.path.join(C.DATA_DIR, "ashare_history_2000", "universe", "*", "alive.csv"))):
        y = os.path.basename(os.path.dirname(p))
        if y not in YEARS:
            continue
        for line in open(p, encoding="utf-8"):
            c = line.strip()
            if c:
                pool.setdefault(c, set()).add(y)
    return pool


def main() -> int:
    a_batch = os.environ.get("CA_MIN")  # 调试用
    pool = load_pool()
    os.makedirs(OUT, exist_ok=True)
    guard = FetchGuard(cap_calls=240, window_s=60, max_retries=2, breaker_threshold=10,
                       breaker_reset_s=600, pacing_s=0.35)
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        print("LOGIN_FAIL", lg.error_code)
        return 6
    codes = sorted(pool)
    report = {"tool": "ca_full_ingest_v2", "n_codes": len(codes), "years": YEARS,
              "counters": {}, "start": time.time(), "events_total": 0}
    done = 0
    for code in codes:
        dest = os.path.join(OUT, f"{code}.events.json")
        if os.path.exists(dest):
            report["counters"]["resumed_skip"] = report["counters"].get("resumed_skip", 0) + 1
            continue
        code_bs = code  # universe alive.csv 已是 sh./sz. 前缀格式
        all_e, errs, ok_years, waits = [], 0, 0, 0
        for y in sorted(pool[code]):
            while True:
                try:
                    all_e += guard.call(events_for_year, bs, code_bs, y, key=f"{code}{y}")
                    ok_years += 1
                    break
                except (RateLimitExceeded, CircuitOpen) as e:
                    waits += 1
                    if waits > 6:
                        print("EARLY_EXIT guard", e, flush=True)
                        bs.logout()
                        _flush(report)
                        return 7
                    t0 = time.time()
                    time.sleep(max(60.0, 0))
                    print(f"  guard-wait@{code}{y} #{waits} ({time.time()-t0:.0f}s)", flush=True)
                except Exception:
                    errs += 1
                    break
        evs = sorted(all_e, key=lambda e_: e_["ex_date"])
        status = ("UNVERIFIED" if ok_years == 0 else
                  "COVERED_EVENTS" if evs else "COVERED_NO_EVENTS")
        tmp = dest + ".tmp"
        json.dump({"_meta": {"source": "baostock.query_dividend_data", "code": code_bs,
                             "years_ok": ok_years, "year_errors": errs, "status": status,
                             "batch": "M4_CA_1998_2019",
                             "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                   "events": evs}, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, dest)
        report["events_total"] += len(evs)
        report["counters"][status] = report["counters"].get(status, 0) + 1
        done += 1
        if done % RELOGIN_EVERY == 0:
            print(f"  scheduled relogin @ {done} @ {time.strftime('%H:%M:%S')}", flush=True)
            _bs_relogin(bs)
        if done % 50 == 0:
            print(f"progress {done}/{len(codes)} events={report['events_total']} @ "
                  f"{time.strftime('%H:%M:%S')}", flush=True)
            _flush(report)
    bs.logout()
    report["elapsed_s"] = round(time.time() - report["start"], 1)
    report["guard"] = guard.stats()
    _flush(report)
    print(json.dumps({"counters": report["counters"], "events_total": report["events_total"],
                      "guard": {k: report["guard"][k] for k in ("ok", "fail", "breaker")}},
                     ensure_ascii=False), flush=True)
    return 0


def _flush(report):
    r = dict(report)
    r["elapsed_s"] = round(time.time() - report["start"], 1)
    tmp = os.path.join(OUT, "coverage_report.json.tmp")
    json.dump(r, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, os.path.join(OUT, "coverage_report.json"))


if __name__ == "__main__":
    raise SystemExit(main())
