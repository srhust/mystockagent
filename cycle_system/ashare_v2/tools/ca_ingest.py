# -*- coding: utf-8 -*-
"""ca_ingest — 包D: 企业行动事件摄取(Baostock官方分红送配→apply_day_events消费格式)。

映射(官方字段→事件):
  cash_per_share = dividCashPsBeforeTax (税前每股); tax_rate=(1-税后/税前) 显式列示
  ratio(BONUS)   = dividStocksPs(送) + dividReserveToStockPs(转) —— 每股新增
  ex_date        = dividOperateDate(除权除息日=生效日)
  regist/pay     = dividRegistDate/dividPayDate(登记/发放, 留存审计)
  source         = "baostock.query_dividend_data" → 加入可信登记
落盘: data/ashare_history_2000/corporate_actions/{code}.events.json (独立数据层)
缺数据年份/字段空 → 不伪造, 事件不生成, 计入 UNVERIFIED 统计(裁定D)。
用法: python -X utf8 tools/ca_ingest.py sh.600000 2000 2010 [--apply-demo]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corporate_actions import apply_day_events                               # noqa: E402
from fetch_guard import FetchGuard                                           # noqa: E402

CA_DIR = os.path.join(C.DATA_DIR, "ashare_history_2000", "corporate_actions")
SRC = "baostock.query_dividend_data"


def fetch_events(code: str, y0: int, y1: int) -> tuple[list, dict]:
    import baostock as bs
    guard = FetchGuard(cap_calls=60, window_s=120, max_retries=2, breaker_threshold=4,
                       breaker_reset_s=120)
    bs.login()
    events, stats = [], {"years": 0, "with_dividend": 0, "zero_field": 0, "errors": {}}
    for yr in range(y0, y1 + 1):
        stats["years"] += 1
        try:
            rs = guard.call(_query_year, bs, code, yr, key=f"{code}:{yr}")
        except Exception as e:                                   # noqa: BLE001
            stats["errors"][f"{type(e).__name__}"] = str(e)[:100]
            continue
        for row in rs:
            cash = float(row.get("dividCashPsBeforeTax") or 0)
            bonus = float(row.get("dividStocksPs") or 0) + float(row.get("dividReserveToStockPs") or 0)
            ex = row.get("dividOperateDate")
            if not ex:
                stats["zero_field"] += 1                            # 除权日缺失=不可应用, 不伪造
                continue
            if cash > 0:
                after = float(row.get("dividCashPsAfterTax") or cash)
                events.append({"code": code.split(".")[1], "type": "DIVIDEND", "ex_date": ex,
                               "cash_per_share": cash,
                               "tax_rate": round(1 - after / cash, 4) if cash else 0.0,
                               "regist": row.get("dividRegistDate"), "pay": row.get("dividPayDate"),
                               "plan_text": row.get("dividCashStock"), "source": SRC})
            if bonus > 0:
                events.append({"code": code.split(".")[1], "type": "BONUS", "ex_date": ex,
                               "ratio": bonus, "plan_text": row.get("dividCashStock"), "source": SRC})
            if cash > 0 or bonus > 0:
                stats["with_dividend"] += 1
    bs.logout()
    events.sort(key=lambda e_: e_["ex_date"])
    stats["guard"] = guard.stats()
    return events, stats


def _query_year(bs, code, yr):
    rs = bs.query_dividend_data(code, str(yr), yearType="report")
    out = []
    while rs.error_code == "0" and rs.next():
        out.append(dict(zip(rs.fields, rs.get_row_data())))
    if rs.error_code != "0":
        raise RuntimeError(f"baostock err {rs.error_code}: {rs.error_msg}")
    return out


def apply_demo(events: list, code: str, buy_day: str) -> dict:
    """单券端到端: 假设buy_day建仓100股, 逐事件apply_day_events, 守恒报告。"""
    st = {"cash": 0.0, "positions": {code.split(".")[1]: 100}}
    mine = [e for e in events if e["ex_date"] >= buy_day]
    new, ap, uv = apply_day_events(st, mine,
                                   registry={"trusted_sources": (SRC, "stock_history_dividend_detail:em")})
    return {"held_from": buy_day, "events_in_window": len(mine),
            "applied": ap, "unverified": uv, "final_state": new}


def main() -> int:
    code, y0, y1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    os.makedirs(CA_DIR, exist_ok=True)
    events, stats = fetch_events(code, y0, y1)
    dest = os.path.join(CA_DIR, f"{code.replace('.','_')}.events.json")
    json.dump({"_meta": {"source": SRC, "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                         "window_years": [y0, y1], "stats": stats,
                         "marking": "除权日缺失/字段空 → 不生成事件, 计入zero_field(UNVERIFIED口径)"},
               "events": events},
              open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    out = {"file": dest, "n_events": len(events), "stats": stats}
    if "--apply-demo" in sys.argv:
        out["apply_demo_2005"] = apply_demo(events, code, "2005-01-04")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:2800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
