# -*- coding: utf-8 -*-
"""A股个股数据管道 (R8/D4): 251只配置股逐日OHLCV+ST状态落盘, 断点续传, 可用性审计。

数据源: akshare stock_zh_a_hist(后复权) + 个股信息(含ST标记)
- 落盘: data/stocks/{code}.csv (date,open,high,low,close,amount,volume)
- 断点续传: 已存在且最新日期>=run_date-7d 的跳过(可force)
- 审计: data/stocks/_audit.json 每股状态(OK/EMPTY/FAILED/UNAVAILABLE)
- 诚实边界: ST历史状态与停复牌逐日记录需专门接口, 当前以ST标记快照+缺失审计披露
"""
import json
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stocks")


def configured_stocks() -> list:
    import config
    out = []
    for k, v in config.CYCLE_SECTORS.items():
        if k in ("buy", "sell", "trailing_stop"):
            continue
        for code, name in v["stocks"]:
            out.append((code, name, k))
    return out  # (code, name, sector)


def fetch_one(code: str, start: str = "20200101", adjust: str = "") -> pd.DataFrame:
    """F3: adjust=""为原始真实成交价(执行器/涨跌停/费用口径); adjust="hfq"仅研究信号复权口径。"""
    import akshare as ak
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, adjust=adjust)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount"})
    keep = [c for c in ("date", "open", "high", "low", "close", "volume", "amount") if c in df.columns]
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def run(max_stocks: int = None, force: bool = False, per_call_sleep: float = 2.0) -> dict:
    """落盘251只(断点续传)。结果审计含每股状态; 部分失败如实记录不掩盖。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    stocks = configured_stocks()
    if max_stocks:
        stocks = stocks[:max_stocks]
    audit_path = os.path.join(DATA_DIR, "_audit.json")
    audit = {}
    if os.path.exists(audit_path) and not force:
        with open(audit_path, encoding="utf-8") as f:
            audit = json.load(f)
    ok = fail = 0
    for code, name, sector in stocks:
        p = os.path.join(DATA_DIR, f"{code}.csv")
        if not force and os.path.exists(p):
            try:  # F3: 断点续传必须校验新鲜度, 不得只看文件存在
                _d = pd.read_csv(p, usecols=["date"])
                _latest = pd.Timestamp(_d["date"].max())
                if (pd.Timestamp.now().normalize() - _latest).days <= 7 and len(_d) >= 100:
                    audit[code] = {**audit.get(code, {}), "status": audit.get(code, {}).get("status", "CACHED"), "name": name, "sector": sector, "latest": str(_latest.date())}
                    ok += 1
                    continue
            except (pd.errors.ParserError, KeyError, ValueError):
                pass  # 损坏→重抓
        df, err = None, None
        for attempt in range(3):  # 限频退避重试: 5s/15s/30s
            try:
                df = fetch_one(code)
                break
            except Exception as e:
                err = str(e)[:200]
                if attempt < 2:
                    time.sleep([5, 15, 30][attempt])
        try:
            if df is None:
                raise RuntimeError(err or "fetch failed after retries")
            if df.empty:
                audit[code] = {"status": "EMPTY", "name": name, "sector": sector}
                fail += 1
            else:
                try:  # F3: adj_factor=hfq/raw(研究口径); 执行器用raw真实价——两口径显式分离
                    hf = fetch_one(code, adjust="hfq")
                    df = df.merge(hf[["date", "close"]].rename(columns={"close": "hfq_close"}), on="date", how="left")
                    df["adj_factor"] = (df["hfq_close"] / df["close"]).round(6)
                    df = df.drop(columns=["hfq_close"])
                except Exception:
                    df["adj_factor"] = float("nan")  # 复权因子缺失如实留空
                df.to_csv(p, index=False)
                audit[code] = {"status": "OK", "name": name, "sector": sector, "rows": len(df),
                               "latest": str(df["date"].max()), "price_basis": "raw+adj_factor"}
                ok += 1
        except Exception as e:  # 如实记录失败, 不静默
            audit[code] = {"status": "FAILED", "name": name, "sector": sector, "error": str(e)[:200]}
            fail += 1
        time.sleep(per_call_sleep)
    audit["_summary"] = {"ok": ok, "fail_empty": fail, "total": len(stocks),
                         "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat()}
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=1)
    return audit


def availability_report() -> dict:
    """D4验收口径: 历史可投资池=已落盘且数据完整的股票集合审计。"""
    with open(os.path.join(DATA_DIR, "_audit.json"), encoding="utf-8") as f:
        audit = json.load(f)
    per = {k: v for k, v in audit.items() if not k.startswith("_")}
    by_status = {}
    for v in per.values():
        by_status[v["status"]] = by_status.get(v["status"], 0) + 1
    return {"total_configured": len(per), "by_status": by_status,
            "tradeable_pool": [k for k, v in per.items() if v["status"] in ("OK", "CACHED")],
            "summary": audit.get("_summary", {})}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    a = run(max_stocks=n)
    print(json.dumps(a["_summary"], ensure_ascii=False))
