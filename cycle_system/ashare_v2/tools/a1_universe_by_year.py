# -*- coding: utf-8 -*-
"""A1 历史存续全集构建器 — 第五阶段裁定A1.

规则(区间语义, 无未来信息):
  券v在年Y存续 ⇔ ipoDate <= Y-12-31 且 (status=1 在两市 OR outDate为空 OR outDate >= Y-01-01)
  - 年内新上市 = ipoDate in Y; 年内退市 = outDate in Y; 年末存续 = ipoDate<=Y-end & (在市|outDate>Y-end)
  - status=1 但实测有停市bar的券(主表diff证据) → outDate缺失不猜测, 标UNKNOWN_OUTDATE(裁定:
    退市时间不完整标UNKNOWN不猜测), 其存续区间终点=UNKNOWN(纳入存续但不作退市日结论)
  - type!=1(指数/ETF等)不进股票universe; code含变更历史无法由basic得知→登记限制声明
输出: QC/universe_by_year.json (每年计数+名单文件指针) + universe/{Y}/alive.csv 名单。
"""
from __future__ import annotations

import csv
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
SRC = os.path.join(ROOT, "security_master", "basic_full_snapshot_2026-10-20.csv")


def load() -> list:
    out = []
    with open(SRC, encoding="utf-8") as f:
        rd = csv.reader(f)
        head = next(rd)
        for r in rd:
            if len(r) >= 6 and r[4] == "1":                    # type=1 股票
                out.append({"code": r[0], "name": r[1], "ipo": r[2],
                            "out": r[3], "status": r[5]})
    return out


def build(years: range) -> dict:
    stocks = load()
    by_code = {s["code"]: s for s in stocks}
    # 主表diff证据不再需要: UNKNOWN定义=status=0(已退市)但outDate缺失(裁定: 退市时间不完整标UNKNOWN)
    unknown_out = [s["code"] for s in stocks
                   if s["status"] == "0" and not s["out"]]
    rep = {"years": {}, "universe_note": "存续判定=ipo/out区间, 无未来信息; 代码变更历史与长停复牌边界限制见A1限制声明",
           "UNKNOWN_OUTDATE": sorted(set(unknown_out))}
    for y in years:
        y0, y1 = f"{y}-01-01", f"{y}-12-31"
        alive = []
        new_l = del_l = unk_alive = 0
        for s in stocks:
            if not s["ipo"] or s["ipo"] > y1:
                continue
            gone_early = bool(s["out"]) and s["out"] < y0
            if gone_early:
                continue
            alive.append(s["code"])
            if y0 <= s["ipo"] <= y1:
                new_l += 1
            if s["out"] and y0 <= s["out"] <= y1:
                del_l += 1
            if not s["out"] and s["status"] == "0":
                unk_alive += 1                       # status=0空out=数据不全, 区间终点UNKNOWN
        d = os.path.join(ROOT, "universe", str(y))
        os.makedirs(d, exist_ok=True)
        dest = os.path.join(d, "alive.csv")
        tmp = dest + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            for c in alive:
                w.writerow([c])
        os.replace(tmp, dest)
        rep["years"][y] = {"alive_any_time_in_year": len(alive), "new_listed": new_l,
                           "delisted_in_year": del_l, "alive_end_year": sum(
                               1 for c in alive
                               if not by_code[c]["out"] or by_code[c]["out"] > y1),
                           "unknown_outdate_alive": unk_alive,
                           "list_file": f"universe/{y}/alive.csv"}
    json.dump(rep, open(os.path.join(ROOT, "QC", "universe_by_year.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return rep


if __name__ == "__main__":
    r = build(range(1998, 2027))
    for y in [1998, 2000, 2002, 2005, 2010, 2015, 2019, 2022, 2026]:
        print(y, r["years"][y])
    print("UNKNOWN_OUTDATE n=", len(r["UNKNOWN_OUTDATE"]), r["UNKNOWN_OUTDATE"][:6])
