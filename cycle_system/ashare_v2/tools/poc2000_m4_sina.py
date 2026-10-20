# -*- coding: utf-8 -*-
"""poc2000_m4_sina — M4 历史日线分批抓取器(sina 通道版, 裁定A2八项全实现).

通道选择记录: baostock单用户全局会话锁+被kill孤儿僵尸占位(实测1.3h未清)且历史通道
限速可疑(600008@2002探针HANG) → 本批走 sina 独立会话通道(akshare.stock_zh_a_daily,
第四阶段已证其历史深度与当日准确性: 10-19对账5/5全等+全池248/251)。baostock保留对账。

与baostock版差异(诚实边界):
  sina返回=**后复权qhfq体系**(close列=hfq), 无 tradestatus/isST 原生字段。
  处置: ①落 raw 口径不可得→本层存 sina 原样并标 ADJUST_BASIS=hfq_sina,
      与既有 raw panel(R31冻结层)交叉核验放批4: 用 adj_factor 反推 raw=hfq/factor 对账;
      ②tradestatus: bar缺失日=当日无bar(停牌推定SUSPENSION_INFERRED, 非官方标记);
      ③isST: 由股票名简称历史不可得→标 ST_FIELD_UNAVAILABLE(sina), 行业/ST证据另源。
A2对照: ①master=A1 alive.csv ②错误三态raise/ERRDATA/空bar事实 ③sha+范围续传
  ④tmp+os.replace ⑤每年manifest+coverage ⑥每100券flush ⑦guard pacing 1.0
  (sina限速严, 第四阶段0.4-1.2s实测) ⑧审批hash绑定复用。
红线: 只写 data/ashare_history_2000/daily_sina/{Y}/; 不覆盖baostock版目录; 不触预测域。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                        # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                   # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_guard import FetchGuard, classify_error                                # noqa: E402
from poc2000_m4_batch import verify_approval, master_for_year                     # noqa: E402 复用①⑧

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
COLS = "date,open,high,low,close,volume,amount"            # sina标准列(hfq域)
COLS_NOTE = ("ADJUST_BASIS=raw_sina(不复权, 与R31 panel同基); 附outstanding_share/turnover列"
             "(股本证据增益); tradestatus缺失日=停牌推定; isST另源")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def sina_symbol(code: str) -> str:
    return ("sh" if code.startswith("sh") else "sz") + code.split(".")[1]


def pull_one(ak, code: str, y: int, guard: FetchGuard):
    import threading
    box = {}

    def _w():
        try:
            df = ak.stock_zh_a_daily(symbol=sina_symbol(code), start_date=f"{y}0101",
                                     end_date=f"{y}1231", adjust="")     # raw口径实测233bar深度
            box["df"] = df
        except Exception as e:
            box["exc"] = e

    def _q():
        t = threading.Thread(target=_w, daemon=True)
        t.start()
        t.join(60)        # 校准: 25s口径批1实测18%假fail(慢查询后台最终成功), 60s贴合sina尾延迟
        if "exc" in box:
            raise box["exc"]                                          # 交guard重试/熔断
        if "df" not in box:
            raise ConnectionError("SINA_HANG_60S")
        df = box["df"]
        return df
    return guard.call(_q)


def write_year(ak, y: int, guard: FetchGuard) -> dict:
    out = os.path.join(ROOT, "daily_sina", str(y))
    os.makedirs(out, exist_ok=True)
    man_p = os.path.join(out, "manifest.json")
    man = json.load(open(man_p, encoding="utf-8")) if os.path.exists(man_p) else \
        {"year": y, "files": {}, "adjust_basis": COLS_NOTE}
    codes = master_for_year(y)
    stats = {"ok": 0, "skip": 0, "fail": 0, "empty": 0, "errors": []}
    t0 = time.time()
    for code in codes:
        dest = os.path.join(out, f"{code}.csv")
        rec = man["files"].get(code)
        if rec and rec.get("note", "").startswith("no_bar") :
            stats["skip"] += 1
            continue
        if rec and rec.get("sha256") and os.path.exists(dest) and sha256(dest) == rec["sha256"] \
           and rec.get("range_first", f"{y}-01-01") >= f"{y}-01-01" \
           and rec.get("range_last", f"{y}-12-31") <= f"{y}-12-31" and rec.get("rows", 0) > 0:
            stats["skip"] += 1
            continue
        try:
            df = pull_one(ak, code, y, guard)
        except Exception as e:
            stats["fail"] += 1
            if len(stats["errors"]) < 400:
                stats["errors"].append(f"{code}:{classify_error(e)}")
            continue
        if df is None or len(df) == 0:
            stats["empty"] += 1
            man["files"][code] = {"rows": 0, "sha256": "",
                                  "note": "no_bar_in_year(停牌/未上市边界事实)"}
            continue
        tmp = dest + ".tmp"
        df.to_csv(tmp, index=False)
        first, last = str(df["date"].iloc[0]), str(df["date"].iloc[-1])
        if not (f"{y}-01-01" <= first <= last <= f"{y}-12-31"):
            os.remove(tmp)
            stats["fail"] += 1
            stats["errors"].append(f"{code}:RANGE_BAD {first}..{last}")
            continue
        os.replace(tmp, dest)
        man["files"][code] = {"rows": len(df), "sha256": sha256(dest),
                              "range_first": first, "range_last": last}
        stats["ok"] += 1
        if (stats["ok"] + stats["skip"]) % 100 == 0:
            mtmp = man_p + ".tmp"
            json.dump(man, open(mtmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
            os.replace(mtmp, man_p)
    man["generated"] = datetime.now().isoformat(timespec="seconds")
    man["stats"] = stats
    man["elapsed_s"] = round(time.time() - t0, 1)
    mtmp = man_p + ".tmp"
    json.dump(man, open(mtmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    os.replace(mtmp, man_p)
    return {"year": y, "alive": len(codes), **stats}


def coverage(y: int) -> dict:
    out = os.path.join(ROOT, "daily_sina", str(y))
    man = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
    codes = master_for_year(y)
    got = sum(1 for c in codes if man["files"].get(c, {}).get("rows", 0) > 0)
    by = json.load(open(os.path.join(ROOT, "QC", "universe_by_year.json"), encoding="utf-8"))["years"][str(y)]
    rows_all = [man["files"][c]["rows"] for c in codes if man["files"].get(c, {}).get("rows", 0) > 0]
    cov = round(got / max(1, by["alive_any_time_in_year"]), 4)
    rep = {"year": y, "channel": "sina", "adjust_basis": COLS_NOTE,
           "指标1_历史存续数": by["alive_any_time_in_year"], "指标2_实获价格数": got,
           "获取率": cov, "指标3_年内上市": by["new_listed"], "指标3_年内退市": by["delisted_in_year"],
           "指标4_ST字段可用率": "ST_FIELD_UNAVAILABLE(sina无isST列, 待baostock对账通道恢复补标)",
           "指标4_停牌推定": "bar缺失日=推定, 官方tradestatus待补",
           "指标5_行数中位": (sorted(rows_all)[len(rows_all) // 2] if rows_all else 0),
           "指标6_公司行动覆盖": "CA事件子批另跑(baostock dividend通道)",
           "指标7_行业PIT覆盖": "第二源p_stock2110实测中(见PIT_INDUSTRY_REPORT)",
           "指标8_FastV可计算率(>=60行)": round(sum(1 for r in rows_all if r >= 60) / max(1, got), 4),
           "指标9_LongCycle可算率(当年>=250行)": round(sum(1 for r in rows_all if r >= 250) / max(1, got), 4),
           "指标10_可信度": "PASS" if cov >= 0.85 else "MAJOR_GAP_SUSPEND"}
    tmp = os.path.join(ROOT, "QC", f"coverage_sina_{y}.json.tmp")
    json.dump(rep, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(ROOT, "QC", f"coverage_sina_{y}.json"))
    return rep


BATCHES = {1: (2000, 2003), 2: (2004, 2010), 3: (2011, 2019), 4: (2020, 2026), 0: (1998, 1999)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, required=True, choices=sorted(BATCHES))
    a = ap.parse_args()
    lo, hi = BATCHES[a.batch]
    verify_approval(list(range(lo, hi + 1)))
    import akshare as ak
    guard = FetchGuard(cap_calls=2400, window_s=3600, max_retries=3, breaker_threshold=8,
                       breaker_reset_s=1200, pacing_s=1.0)
    bad = []
    for y in range(lo, hi + 1):
        st = write_year(ak, y, guard)
        cv = coverage(y)
        print(y, "pull:", {k: st[k] for k in ("alive", "ok", "skip", "fail", "empty")},
              "| cov:", cv["获取率"], "| 可信度:", cv["指标10_可信度"], flush=True)
        if cv["指标10_可信度"] != "PASS":
            bad.append(y)
    if bad:
        print(f"批{a.batch}缺口年份{bad} → 暂停自动推进报告。")
        return 8
    print(f"批{a.batch} sina通道QC通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
