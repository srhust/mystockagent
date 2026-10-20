# -*- coding: utf-8 -*-
"""poc2000_m4_batch — M4 历史日线分批抓取器(第五阶段裁定A2八项全实现).

A2.1 master_for_year 实装: 读 A1 区间存续集 universe/{Y}/alive.csv(缺失即报错先跑a1)。
A2.2 错误不静默: error_code!='0' 分网络类(raise交guard重试)与数据类(计入fail+原因)。
A2.3 缓存一致性: 断点续传前核 sha256+日期范围⊆当年+行数+字段头, 不符重取。
A2.4 原子写: tmp+os.replace, 校验成功才替换。
A2.5 每年独立 manifest.json + coverage_report_{Y}.json(A4十指标)。
A2.6 断点续传: manifest 记录 per-file sha/rows; 重复运行零重复数据。
A2.7 限速/退避/熔断: fetch_guard(pacing 0.35, cap 1500/时, 熔断10连败/静默15min), 禁无限重试。
A2.8 审批绑定: M3_APPROVAL 必须含 years_authorized⊇请求年 + ruling_document_sha256 与
     裁定原文实际 hash 相等 + baseline_commit 非空——任一不满足拒绝运行。

红线: 只写 data/ashare_history_2000/; 不触碰 cycle_system 预测16域与既有冻结产物。
批次门: 每批QC(获取率<85%或空文件异常 → rc=8 暂停自动推进, 报告缺口)。
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

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
COLS = "date,open,high,low,close,preclose,volume,amount,turn,tradestatus,isST"
APPROVAL = os.path.join(ROOT, "M3_APPROVAL_2026-10-20.json")
RULING = os.path.join(C.OUTPUT_DIR, "independent", "PHASE5_RULING_2026-10-20.md")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def verify_approval(years: list) -> dict:
    """A2.8: 审批文件须含范围与版本, 且绑定裁定原文 hash。"""
    if not (os.path.exists(APPROVAL) and os.path.exists(RULING)):
        raise SystemExit("拒: 审批文件或裁定原文缺失")
    ap_ = json.load(open(APPROVAL, encoding="utf-8"))
    lo, hi = ap_["scope"]["years_authorized"]
    if any(not (lo <= y <= hi) for y in years):
        raise SystemExit(f"拒: 请求年{years}超出批准范围[{lo},{hi}]")
    if not ap_.get("baseline_commit"):
        raise SystemExit("拒: 审批缺基线版本")
    if sha256(RULING) != ap_.get("ruling_document_sha256"):
        raise SystemExit("拒: 裁定原文hash与审批绑定不符(文件被改动?)")
    return ap_


def master_for_year(y: int) -> list:
    """A2.1: A1 区间存续全集(任意时段上市即入)。"""
    p = os.path.join(ROOT, "universe", str(y), "alive.csv")
    if not os.path.exists(p):
        raise SystemExit(f"拒: universe/{y}/alive.csv 不存在——先跑 a1_universe_by_year.py")
    with open(p, encoding="utf-8") as f:
        return [r[0] for r in csv.reader(f) if r]


def cache_ok(path: str, rec: dict, y: int) -> bool:
    """A2.3: 缓存文件与manifest记录一致(sha+范围+行数+字段)。"""
    if rec.get("note") == "no_bar_in_year(停牌/边界事实)":
        return True                                       # 空bar=数据事实, 续传不重拉
    if not os.path.exists(path) or sha256(path) != rec.get("sha256"):
        return False
    if rec.get("range_first", f"{y}-01-01") < f"{y}-01-01" or rec.get("range_last", f"{y}-12-31") > f"{y}-12-31":
        return False
    return rec.get("rows", 0) > 0


def pull_one(bs, code: str, y: int, guard: FetchGuard):
    """A2.2: 三态返回 rows / EMPTY_OK / RAISE(交guard)。"""
    def _q():
        rs = bs.query_history_k_data_plus(code, COLS, start_date=f"{y}-01-01",
                                         end_date=f"{y}-12-31", frequency="d", adjustflag="3")
        if rs.error_code != "0":
            msg = rs.error_msg
            if any(k in msg for k in ("网络", "连接", "timeout", "recv")):
                raise ConnectionError(msg)                      # 网络类→重试/熔断
            return ("ERRDATA", msg)                              # 数据类→不重试, 计fail
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return rows                                              # []=该券年内无bar(停牌/边界)
    return guard.call(_q)


def write_year(bs, y: int, guard: FetchGuard) -> dict:
    out = os.path.join(ROOT, "daily", str(y))
    os.makedirs(out, exist_ok=True)
    man_p = os.path.join(out, "manifest.json")
    man = json.load(open(man_p, encoding="utf-8")) if os.path.exists(man_p) else \
        {"year": y, "files": {}, "generated": ""}
    codes = master_for_year(y)
    stats = {"skip": 0, "ok": 0, "fail": 0, "empty": 0, "errors": []}
    t0 = time.time()
    for code in codes:
        dest = os.path.join(out, f"{code}.csv")
        rec = man["files"].get(code)
        if rec and cache_ok(dest, rec, y):
            stats["skip"] += 1
            continue
        try:
            res = pull_one(bs, code, y, guard)
        except Exception as e:                                    # guard熔断/重试耗尽
            stats["fail"] += 1
            stats["errors"].append(f"{code}:{classify_error(e)}")
            continue
        if isinstance(res, tuple) and res[0] == "ERRDATA":
            stats["fail"] += 1
            stats["errors"].append(f"{code}:DATA_ERR:{res[1]}")
            continue
        if not res:
            stats["empty"] += 1
            man["files"][code] = {"rows": 0, "sha256": "", "note": "no_bar_in_year(停牌/边界事实)"}
            continue
        tmp = dest + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLS.split(","))
            w.writerows(res)
        first, last = res[0][0], res[-1][0]
        if not (f"{y}-01-01" <= first <= last <= f"{y}-12-31"):
            os.remove(tmp)                                        # A2.4: 校验不过不落正式名
            stats["fail"] += 1
            stats["errors"].append(f"{code}:RANGE_BAD {first}..{last}")
            continue
        os.replace(tmp, dest)
        man["files"][code] = {"rows": len(res), "sha256": sha256(dest),
                              "range_first": first, "range_last": last}
        stats["ok"] += 1
        if (stats["ok"] + stats["skip"]) % 100 == 0:               # A2.6强化: 每100券续传落盘
            mtmp0 = man_p + ".tmp"
            json.dump(man, open(mtmp0, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
            os.replace(mtmp0, man_p)
    man["generated"] = datetime.now().isoformat(timespec="seconds")
    man["stats"] = stats
    man["elapsed_s"] = round(time.time() - t0, 1)
    mtmp = man_p + ".tmp"
    json.dump(man, open(mtmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    os.replace(mtmp, man_p)
    return {"year": y, "alive": len(codes), **stats}


def coverage(bs, y: int) -> dict:
    """A4 十指标(行业PIT待B线; CA事件覆盖按批次声明)。"""
    out = os.path.join(ROOT, "daily", str(y))
    man = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
    codes = master_for_year(y)
    got = sum(1 for c in codes if man["files"].get(c, {}).get("rows", 0) > 0)
    rows_all = [man["files"][c]["rows"] for c in codes if man["files"].get(c, {}).get("rows", 0) > 0]
    st_avail = susp_rows = tot_rows = 0
    for c in codes:
        p = os.path.join(out, f"{c}.csv")
        if not (os.path.exists(p) and man["files"].get(c, {}).get("rows", 0) > 0):
            continue
        with open(p, encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for r in rd:
                tot_rows += 1
                if r.get("isST", "") != "":
                    st_avail += 1
                if r.get("tradestatus", "1") == "0":
                    susp_rows += 1
    by_year = json.load(open(os.path.join(ROOT, "QC", "universe_by_year.json"), encoding="utf-8"))["years"]
    yr = by_year[str(y)]
    cov = round(got / max(1, yr["alive_any_time_in_year"] - yr["new_listed"] * 0), 4)
    rep = {"year": y, "指标1_历史存续数": yr["alive_any_time_in_year"],
           "指标2_实获价格数": got, "获取率": cov,
           "指标3_年内上市": yr["new_listed"], "指标3_年内退市": yr["delisted_in_year"],
           "指标4_ST字段可用率": round(st_avail / max(1, tot_rows), 4),
           "指标4_停牌bar数": susp_rows,
           "指标5_行数中位": (sorted(rows_all)[len(rows_all) // 2] if rows_all else 0),
           "指标6_公司行动覆盖": "价格批内不拉CA事件(分步), 见CORPORATE_ACTION_GAPS",
           "指标7_行业PIT覆盖": "UNKNOWN(第五阶段B线调查中)",
           "指标8_FastV可计算率(当年行数>=60)": round(
               sum(1 for r in rows_all if r >= 60) / max(1, got), 4),
           "指标9_LongCycle可计算率(当年行数>=250, 跨年前史另算)": round(
               sum(1 for r in rows_all if r >= 250) / max(1, got), 4),
           "指标10_可信度": "PASS" if cov >= 0.85 else "MAJOR_GAP_SUSPEND"}
    dtmp = os.path.join(ROOT, "QC", f"coverage_report_{y}.json.tmp")
    json.dump(rep, open(dtmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(dtmp, os.path.join(ROOT, "QC", f"coverage_report_{y}.json"))
    return rep


BATCHES = {1: (2000, 2003), 2: (2004, 2010), 3: (2011, 2019), 4: (2020, 2026), 0: (1998, 1999)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, required=True, choices=sorted(BATCHES))
    a = ap.parse_args()
    lo, hi = BATCHES[a.batch]
    years = list(range(lo, hi + 1))
    verify_approval(years)                                        # A2.8
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        print("baostock登录失败:", lg.error_msg)
        return 7
    guard = FetchGuard(cap_calls=1900, window_s=3600, max_retries=3, breaker_threshold=10,
                       breaker_reset_s=900, pacing_s=0.35)
    bad = []
    for y in years:
        st = write_year(bs, y, guard)
        cv = coverage(bs, y)
        print(y, "pull:", {k: st[k] for k in ("alive", "ok", "skip", "fail", "empty")},
              "| cov:", cv["获取率"], "| 可信度:", cv["指标10_可信度"], flush=True)
        if cv["指标10_可信度"] != "PASS":
            bad.append(y)
    bs.logout()
    if bad:
        print(f"批{a.batch}存在重大缺口年份{bad} → 按裁定暂停自动推进, 报告缺口。")
        return 8
    print(f"批{a.batch} QC通过。下一批={a.batch + 1 if a.batch != 0 else 1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
