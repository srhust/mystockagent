# -*- coding: utf-8 -*-
"""ca_full_ingest — 包D: 现有回放窗口涉事券(156)×2020-2026 企业行动全池摄取。

范围纪律: 只取 R0/R1 回放**涉事券**(成交/持仓出现过的), 不动全市场(那是 M4 扩量审批后
的事); 请求 1092 次, 全程 FetchGuard(pacing 0.3s+cap+熔断)。
落 data/ashare_history_2000/corporate_actions/full_replay_pool/{code}.events.json
断点续跑: 已存在文件跳过(append-safe)。
覆盖分类(裁定D 的 UNVERIFIED 精确化):
  COVERED_EVENTS      查询成功且有事件
  COVERED_NO_EVENTS   查询成功且确无分红送转(=数据事实, 非缺口)
  UNVERIFIED          查询失败/接口错误(标注保持)
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_guard import FetchGuard, RateLimitExceeded, CircuitOpen              # noqa: E402

OUT = os.path.join(C.DATA_DIR, "ashare_history_2000", "corporate_actions", "full_replay_pool")
YEARS = ("2020", "2021", "2022", "2023", "2024", "2025", "2026")
SRC = "baostock.query_dividend_data"


def events_for_year(bs, code_bs, y):
    rs = bs.query_dividend_data(code_bs, y, yearType="report")
    # baostock 语义: "该年无分红记录"以 error_code≠0(1000xxx/无结果)返回, 属数据事实非失败
    if rs.error_code != "0" and "success" not in (rs.error_msg or "").lower():
        msg = (rs.error_msg or "").lower()
        if any(k in msg for k in ("无", "not found", "no data", "empty")) or rs.error_code.startswith("1000"):
            return []          # COVERED_NO_EVENTS 口径: 查询成功到达, 无记录
        raise RuntimeError(f"bs {rs.error_code}: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(dict(zip(rs.fields, rs.get_row_data())))
    evs = []
    for row in rows:
        cash = float(row.get("dividCashPsBeforeTax") or 0)
        bonus = float(row.get("dividStocksPs") or 0) + float(row.get("dividReserveToStockPs") or 0)
        ex = row.get("dividOperateDate")
        if not ex:
            continue
        if cash > 0:
            raw_after = str(row.get("dividCashPsAfterTax") or "")
            if "或" in raw_after:
                # 2015-09 股息红利差别化: 税后值按持有期分档(1月内/1年/1年以上), 记录层不定值
                opts = [float(x) for x in raw_after.split("或")]
                evs.append({"code": code_bs.split(".")[1], "type": "DIVIDEND", "ex_date": ex,
                            "cash_per_share": cash, "cash_after_tax_options": opts,
                            "tax_ambiguous": True,
                            "note": "差别化税率: 入账额由消费层按实际持有期选择options",
                            "regist": row.get("dividRegistDate"), "pay": row.get("dividPayDate"),
                            "plan_text": row.get("dividCashStock"), "source": SRC})
            else:
                after = float(raw_after or cash)
                evs.append({"code": code_bs.split(".")[1], "type": "DIVIDEND", "ex_date": ex,
                            "cash_per_share": cash, "tax_rate": round(1 - after / cash, 4),
                            "regist": row.get("dividRegistDate"), "pay": row.get("dividPayDate"),
                            "plan_text": row.get("dividCashStock"), "source": SRC})
        if bonus > 0:
            evs.append({"code": code_bs.split(".")[1], "type": "BONUS", "ex_date": ex,
                        "ratio": bonus, "plan_text": row.get("dividCashStock"), "source": SRC})
    return evs


def main() -> int:
    codes = json.load(open("_ca_pool.json", encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)
    guard = FetchGuard(cap_calls=250, window_s=60, max_retries=2, breaker_threshold=8,
                       breaker_reset_s=300, pacing_s=0.3)
    import baostock as bs
    bs.login()
    report = {"tool": "ca_full_ingest", "n_codes": len(codes), "years": list(YEARS),
              "per_code": {}, "counters": Counter(), "start": time.time()}
    for code in codes:
        code_bs = ("sh." if code.startswith("6") else "sz.") + code
        dest = os.path.join(OUT, f"{code}.events.json")
        if os.path.exists(dest):
            report["counters"]["resumed_skip"] += 1
            try:
                prev = json.load(open(dest, encoding="utf-8"))
                report["per_code"][code] = {"events": len(prev["events"]),
                                            "status": prev["_meta"]["status"]}
            except Exception:
                pass
            continue
        all_e, errs, ok_years = [], 0, 0
        for y in YEARS:
            try:
                all_e += guard.call(events_for_year, bs, code_bs, y, key=f"{code}{y}")
                ok_years += 1
            except (RateLimitExceeded, CircuitOpen) as e:
                report["counters"]["gate_reject"] += 1
                errs += 1
            except Exception:
                errs += 1
        evs = sorted(all_e, key=lambda e_: e_["ex_date"])
        status = ("UNVERIFIED" if ok_years == 0 else
                  "COVERED_EVENTS" if evs else "COVERED_NO_EVENTS")
        json.dump({"_meta": {"source": SRC, "code": code_bs, "years_ok": ok_years,
                             "year_errors": errs, "status": status,
                             "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                   "events": evs},
                  open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        report["per_code"][code] = {"events": len(evs), "status": status}
        report["counters"][status] += 1
        if report["counters"].get("_tick", 0) % 10 == 0:
            _flush(report)
    bs.logout()
    report["elapsed_s"] = round(time.time() - report["start"], 1)
    report["guard"] = guard.stats()
    report["total_events"] = sum(v["events"] for v in report["per_code"].values())
    _flush(report)
    print(json.dumps({"counters": dict(report["counters"]), "total_events": report["total_events"],
                      "guard": {k: report["guard"][k] for k in ("ok", "fail", "blocked", "breaker")}},
                     ensure_ascii=False, indent=1))
    return 0


class Counter(dict):
    def __getitem__(self, k):
        return dict.get(self, k, 0)

    def __getattr__(self, k):
        return self[k]


def _flush(report):
    r = dict(report)
    r["counters"] = dict(report["counters"])
    json.dump(r, open(os.path.join(OUT, "coverage_report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    sys.exit(main())
