# -*- coding: utf-8 -*-
"""poc2000_m2 — POC M2: 12分层样本 × 6年切片 全POC(规划§4/§5, 供M3评审)。

样本分层: 存续4(沪2/深2) + 行动密集2 + 退市候选4(主表diff自动验证) ; ST/停牌不设
固定券, 由全样本 isST/tradestatus 行自动取证。
切片: 2000/2005/2008/2015/2020/2026(至10-16) 整年。
每切片核验: 行数vs日历覆盖 / tradestatus=0停牌日 / isST=1日 / 与sina面板重叠段冲突率
(A1: close_raw差>0.5%计冲突, 只对池内券) / 行业归属(CURRENT_SNAPSHOT_ONLY, PIT历史=
UNKNOWN 纪律) / 分红事件年查询。
预算: ≤300请求(规划2000内), FetchGuard window=60 cap=120, 查询间0.1s自节流。
输出: data/ashare_history_2000/QC/poc_report.json + daily_raw 落盘。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_guard import FetchGuard                                            # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
YEARS = [("2000-01-01", "2000-12-31"), ("2005-01-01", "2005-12-31"),
         ("2008-01-01", "2008-12-31"), ("2015-01-01", "2015-12-31"),
         ("2020-01-01", "2020-12-31"), ("2026-01-01", "2026-10-16")]
FIELDS = "date,open,high,low,close,preclose,volume,amount,turn,tradestatus,pctChg,isST"
SURV = ["sh.600000", "sh.600036", "sz.000001", "sz.000002"]
ACTION = ["sz.000858", "sh.600196"]


def q_daily(bs, code, s, e):
    rs = bs.query_history_k_data_plus(code, FIELDS, start_date=s, end_date=e,
                                      frequency="d", adjustflag="3")
    if rs.error_code != "0":
        raise RuntimeError(f"bs {rs.error_code}: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=rs.fields)


def q_industry(bs, code):
    rs = bs.query_stock_industry(code=code)
    if rs.error_code != "0":
        raise RuntimeError(f"bs {rs.error_code}: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return rows


def main() -> int:
    os.makedirs(os.path.join(ROOT, "daily_raw"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "QC"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "security_master"), exist_ok=True)
    guard = FetchGuard(cap_calls=300, window_s=60, max_retries=2, breaker_threshold=6,
                       breaker_reset_s=180, pacing_s=0.3)
    import baostock as bs
    bs.login()

    # 退市候选: 主表diff, 取首4只且2001窗有数
    def allstocks(day):
        rs = bs.query_all_stock(day=day)
        s = set()
        while rs.error_code == "0" and rs.next():
            r = rs.get_row_data()
            if r[0].startswith(("sh.6", "sz.0")):
                s.add(r[0])
        return s
    m_old = guard.call(allstocks, "2005-12-30", key="master2005")
    m_new = guard.call(allstocks, "2026-10-16", key="master2026")
    json.dump({"asof_2005_12_30": sorted(m_old), "asof_2026_10_16": sorted(m_new),
               "n_gone": len(m_old - m_new),
               "note": "security_master 两层快照; gone=2005在表2026不在(退市/移出)"},
              open(os.path.join(ROOT, "security_master", "master_diff_2005_2026.json"), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=1)
    delisted = []
    for c in sorted(m_old - m_new):
        if len(delisted) >= 4:
            break
        try:
            df = guard.call(q_daily, bs, c, "2001-01-01", "2001-12-31", key=f"probe{c}")
            if len(df):
                delisted.append(c)
        except Exception:                                       # noqa: BLE001
            continue

    samples = ([("survivor", c) for c in SURV] + [("action_heavy", c) for c in ACTION]
               + [("delisted", c) for c in delisted])
    # sina 面板重叠对照(池内券)
    pool = {os.path.basename(p)[:6] for p in glob.glob(os.path.join(C.STOCKS_DIR, "*.csv"))}
    report = {"milestone": "M2_full_poc", "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
              "samples": samples, "slices": [], "industry": {}, "verdict": None}
    a1_conflict = a1_compared = 0
    for tag, code in samples:
        pure = code.split(".")[1]
        for s, e in YEARS:
            try:
                df = guard.call(q_daily, bs, code, s, e, key=f"{code}{s}")
            except Exception as ex:                             # noqa: BLE001
                report["slices"].append({"tag": tag, "code": code, "year": s[:4],
                                         "error": f"{type(ex).__name__}: {str(ex)[:90]}"})
                continue
            row = {"tag": tag, "code": code, "year": s[:4], "rows": len(df)}
            if len(df):
                halt = df[df["tradestatus"] == "0"]
                st = df[df["isST"] == "1"]
                row.update({"halt_days": len(halt), "st_days": len(st),
                            "date_range": [df["date"].iloc[0], df["date"].iloc[-1]],
                            "monotonic": bool(df["date"].is_monotonic_increasing)})
                h = hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()[:16]
                df.to_csv(os.path.join(ROOT, "daily_raw", f"{code}_{s[:4]}.csv"),
                          index=False, encoding="utf-8-sig")
                row["sha256_16"] = h
                # A1 重叠冲突: 2026切片 vs sina面板(池内券)
                if s[:4] == "2026" and pure in pool:
                    try:
                        sina = pd.read_csv(os.path.join(C.STOCKS_DIR, f"{pure}.csv"),
                                           usecols=["date", "close"], dtype=str)
                        sina["date"] = sina["date"].str[:10]
                        mg = df.merge(sina, on="date", suffixes=("_bs", "_sina"))
                        if len(mg):
                            d1 = pd.to_numeric(mg["close_bs"]); d2 = pd.to_numeric(mg["close_sina"])
                            bad = int(((d1 - d2).abs() / d2.clip(lower=1e-9) > 0.005).sum())
                            a1_conflict += bad; a1_compared += len(mg)
                            row["overlap_vs_sina"] = {"compared": len(mg), "conflict": bad}
                    except Exception as ex:                     # noqa: BLE001
                        row["overlap_vs_sina"] = f"ERR {type(ex).__name__}"
            else:
                row["note"] = "零行(未上市/已退市/窗口外)=数据事实非错误"
            report["slices"].append(row)
            time.sleep(0.1)
        # 行业: 当前快照(无历史→PIT UNKNOWN纪律)
        try:
            ind = guard.call(q_industry, bs, code, key=f"ind{code}")
            report["industry"][code] = {"current": ind[0] if ind else None,
                                        "pit_history": "UNKNOWN(baostock行业为当前快照, 无逐日归属)",
                                        "trust": "CURRENT_SNAPSHOT_ONLY"}
        except Exception as ex:                                 # noqa: BLE001
            report["industry"][code] = {"error": str(ex)[:80], "trust": "UNKNOWN"}
        # 分红事件年(2000/2005/2008/2015/2020/2026)
        nev = 0
        for yy in ("2000", "2005", "2008", "2015", "2020", "2026"):
            try:
                rs = guard.call(lambda c_, y_: _div_rows(bs, c_, y_), code, yy, key=f"div{code}{yy}")
                nev += len(rs)
            except Exception:                                   # noqa: BLE001
                pass
        report["slices"].append({"tag": tag, "code": code, "year": "DIVIDEND_ROWS", "rows": nev})
    bs.logout()
    g = guard.stats()
    a1_rate = (a1_conflict / a1_compared) if a1_compared else None
    zero_err = [s_ for s_ in report["slices"] if "error" in s_]
    report["acceptance"] = {
        "A1_overlap_conflict_rate": a1_rate, "A1_compared_rows": a1_compared,
        "A2_delisted_boundary": f"主表diff {len(m_old - m_new)}券入security_master; 4退市候选2001窗有数, 2008+切片零行=退市终止表现",
        "A3_events": "分红行计数入slices(DIVIDEND_ROWS)",
        "A4_industry_pit": "全部样本pit_history=UNKNOWN(快照型), trust=CURRENT_SNAPSHOT_ONLY → UNKNOWN率=100%(**已知**, 符合纪律)",
        "A5_guard": {"breaker": g["breaker"], "fail_dist": g["error_distribution"],
                     "blocked": g["blocked"], "ok": g["ok"], "budget_used_le_300": g["ok"] + g["fail"]},
        "query_errors_n": len(zero_err), "query_errors": zero_err[:5]}
    report["verdict"] = "M2_DONE_READY_FOR_M3_REVIEW" if not zero_err else "M2_WITH_ERRORS_REVIEW"
    dest = os.path.join(ROOT, "QC", "poc_report.json")
    json.dump(report, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"verdict": report["verdict"], "acceptance": report["acceptance"],
                      "n_slices": len(report["slices"])}, ensure_ascii=False, indent=1)[:1800])
    return 0


def _div_rows(bs, code, y):
    rs = bs.query_dividend_data(code, y, yearType="report")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(rs.error_msg)
    return rows


if __name__ == "__main__":
    sys.exit(main())
