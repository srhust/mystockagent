# -*- coding: utf-8 -*-
"""A股数据管道审计: 30行业→ETF代理行情覆盖矩阵 + 缺口审计(Release Gate主攻线A)。

- 行业→代理: config.ETF_MAP (26行业); 无代理3行业(造纸/航运港口/航空机场)如实标记
- 每代理CSV: 最新日期/行数/对日历缺口数/滞后交易日数
- ST与停复牌: 个股级行情字段当前缺失(本地无个股CSV), 如实标记待接入
输出: output/ashare/data_audit.md + data_audit.json
"""
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ashare_calendar as cal

DATA_DIR = cal.DATA_DIR
OUT_DIR = os.path.join(os.path.dirname(DATA_DIR), "output", "ashare")


def _csv_name(code: str) -> str:
    pfx = "sh" if code.endswith(".SH") else "sz"
    return f"etf_{code}" if "." in code else None


def audit(max_lag_days: int = 5) -> dict:
    calendar = cal.build_cache(force=False)
    days = sorted(pd.to_datetime(calendar["date"]))
    last_mkt_day = days[-1]
    rows, missing_csv = [], []
    for sector, (sina_code, name, note) in __import__("config").ETF_MAP.items():
        p = os.path.join(DATA_DIR, f"etf_{sina_code}.csv")
        if not os.path.exists(p):
            missing_csv.append(sector)
            continue
        df = pd.read_csv(p)
        d = pd.to_datetime(df["date"]).dt.normalize()
        latest = d.max()
        didx = pd.DatetimeIndex(days)
        gaps = sorted(set(didx[(didx > d.min()) & (didx < latest)]) - set(d))
        lag = len(didx[didx > latest])
        rows.append({
            "sector": sector, "proxy": sina_code, "name": name,
            "rows": len(df), "first": str(d.min().date()), "latest": str(latest.date()),
            "lag_trading_days": lag, "gaps_within": len(gaps),
            "ok": lag <= max_lag_days and len(gaps) == 0,
        })
    cfg = __import__("config")
    all_sectors = [k for k in cfg.CYCLE_SECTORS if k not in ("buy", "sell", "trailing_stop")]
    stocks_total = sum(len(v["stocks"]) for k, v in cfg.CYCLE_SECTORS.items() if k in all_sectors)
    out = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "calendar": cal.validate_cross_check(),
        "market_last_day": str(last_mkt_day.date()),
        "sectors_total": len(all_sectors), "stocks_total": stocks_total,
        "etf_proxies": rows, "no_proxy_sectors": [s for s in all_sectors if s not in cfg.ETF_MAP],
        "missing_csv_sectors": missing_csv,
        "individual_stock_data": "ABSENT: 本地无个股CSV, ST/停复牌状态需接入个股行情源(后续)",
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "data_audit.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    with open(os.path.join(OUT_DIR, "data_audit.md"), "w", encoding="utf-8") as f:
        f.write(f"# A股数据管道审计 ({out['generated_at'][:10]})\n\n")
        f.write(f"- 市场日历: {out['calendar']['range'][0]} ~ {out['calendar']['range'][1]} 共{out['calendar']['days']}个交易日; 官方节假日交叉校验通过\n")
        f.write(f"- 行业: {out['sectors_total']} 个(个股池{stocks_total}只); ETF代理: {len(rows)}/{len(all_sectors)}; 无代理: {out['no_proxy_sectors']}\n\n")
        f.write("| 行业 | 代理 | 行数 | 首日 | 最新 | 滞后(交易日) | 日历内缺口 | 状态 |\n|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['sector']} | {r['proxy']} | {r['rows']} | {r['first']} | {r['latest']} | {r['lag_trading_days']} | {r['gaps_within']} | {'OK' if r['ok'] else 'CHECK'} |\n")
        f.write(f"\n- 缺CSV行业: {missing_csv or '无'}; 个股级ST/停复牌: 待接入\n")
    return out


if __name__ == "__main__":
    o = audit()
    print(f"OK 日历{o['calendar']['days']}天 代理{len(o['etf_proxies'])} 无代理{o['no_proxy_sectors']}")
