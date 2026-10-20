# -*- coding: utf-8 -*-
"""p2110_pit_batch — 行业归属历史(p_stock2110)批量采集器(裁定B主第二源, cninfo独立通道).

输入: universe/{Y}/alive.csv 并集(去重)。输出: industry_p2110/{code}.csv(逐券原始记录,
标准×变更日期×行业码) + manifest.json。消费侧按 [变更日期,下条同标准变更日期) 构造区间,
头前=UNKNOWN(PIT_INDUSTRY_REPORT §2)。退市券实测零记录→EMPTY_DELISTED标注不猜测。
卫生: 复用批器纪律(线程30s有界/原子写/续传skip/pacing1.5s/熔断)。价格行业接口独立✓。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                        # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                    # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
OUT = os.path.join(ROOT, "industry_p2110")


def load_codes(years):
    s = set()
    for y in years:
        p = os.path.join(ROOT, "universe", str(y), "alive.csv")
        s.update(l.strip() for l in open(p, encoding="utf-8") if l.strip())
    return sorted(s)


def fetch_one(ak, code: str, bound=30):
    """返回 (df_or_None, err). 空记录(退市券实测)=('EMPTY', '') 区分于错误。"""
    box = {}

    def _w():
        try:
            num = code.split(".")[1]
            df = ak.stock_industry_change_cninfo(symbol=num, start_date="19900101",
                                                 end_date="20261231")
            box["df"] = df
        except KeyError as e:
            box["empty"] = str(e)          # 实测: 零记录路径KeyError'变更日期'
        except Exception as e:
            box["exc"] = f"{type(e).__name__}:{str(e)[:60]}"

    t = threading.Thread(target=_w, daemon=True)
    t.start()
    t.join(bound)
    if "exc" in box:
        return None, box["exc"]
    if "empty" in box:
        return "EMPTY", ""
    if "df" not in box:
        return None, f"HANG_{bound}S"
    return box["df"], ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", required=True, help="逗号分隔, 如 2000,2001,2002,2003")
    ap.add_argument("--limit", type=int, default=0, help="试点券数上限(0=全量)")
    a = ap.parse_args()
    years = [int(x) for x in a.years.split(",")]
    os.makedirs(OUT, exist_ok=True)
    man_p = os.path.join(OUT, "manifest.json")
    man = json.load(open(man_p, encoding="utf-8")) if os.path.exists(man_p) else {"files": {}}
    codes = load_codes(years)
    if a.limit:
        codes = codes[:a.limit]
    import akshare as ak
    ok = skip = empty = fail = 0
    t0 = time.time()
    hang_streak = 0
    for code in codes:
        if code in man["files"]:
            skip += 1
            continue
        df, err = fetch_one(ak, code)
        if err:
            fail += 1
            hang_streak = hang_streak + 1 if "HANG" in err else 0
            man.setdefault("errors", []).append(f"{code}:{err}")
            if hang_streak >= 5:
                print("CIRCUIT_BREAK 连续5HANG, 速退续传")
                break
            time.sleep(2.0)
            continue
        hang_streak = 0
        if isinstance(df, str) and df == "EMPTY":
            empty += 1
            man["files"][code] = {"note": "EMPTY_DELISTED_OR_UNREGISTERED", "rows": 0}
        else:
            dest = os.path.join(OUT, f"{code}.csv")
            tmp = dest + ".tmp"
            df.to_csv(tmp, index=False, encoding="utf-8-sig")
            os.replace(tmp, dest)
            ok += 1
            man["files"][code] = {"rows": len(df), "cols": list(df.columns)[:6],
                                  "range_first": str(df.iloc[0].get("变更日期", ""))[:10]}
        time.sleep(1.5)
        if (ok + empty + skip) % 50 == 0:
            mtmp = man_p + ".tmp"
            json.dump(man, open(mtmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
            os.replace(mtmp, man_p)
            print(f"progress ok{ok} empty{empty} skip{skip} fail{fail} @{round(time.time()-t0)}s",
                  flush=True)
    man["generated"] = datetime.now().isoformat(timespec="seconds")
    man["stats"] = {"ok": ok, "skip": skip, "empty": empty, "fail": fail,
                    "elapsed_s": round(time.time() - t0)}
    mtmp = man_p + ".tmp"
    json.dump(man, open(mtmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    os.replace(mtmp, man_p)
    print("DONE", man["stats"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
