# -*- coding: utf-8 -*-
"""forward_ingest — 前向增量数据层(第二轮复核P0-2)。

纪律:
  * 冻结历史(R31 manifest所辖CSV)永不改写/追加: 前向新交易日**只**进本增量层;
  * 每日文件自带审计: meta.json{fetched_at, source, rows, sha256(csv), codes_missing};
  * 旧manifest不可改写, 校验不可静默关闭: load_merged对增量逐日验sha256, 冻结层仍走
    DataPortal原verify_hashes;
  * 双口径显式分离(与冻结管道F3一致): raw open/close=执行/涨跌停/费用域, close_adj=研究信号域。
  * plan日要求收盘后抓取(fetched_at≥该日15:00)——盘中close不可信。

用法:
  python -X utf8 cycle_system/ashare_v2/forward_ingest.py fetch --day 2026-10-19 [--codes 600028,...]
  (默认股票池 = 冻结管道 configured_stocks(); adj_factor=hfq/raw 两次抓取, 只读复用fetch_one)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from cycle_system.ashare_v2 import _path                                   # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                             # noqa: E402

ING_DIR = os.path.join(C.OUTPUT_DIR, "forward", "incremental")
TZ = dt.timezone(dt.timedelta(hours=8))


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with io.open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def fetch_day(day: str, codes: list | None = None, per_call_sleep: float = 0.4,
              force: bool = False) -> dict:
    """抓取day日bar(akshare同源, 复用冻结管道fetch_one只读): raw+hfq两口径。

    幂等: 当日csv+meta已存在且哈希自洽→直接返回既有meta(保留最早fetched_at证据,
    不重抓500接口); 数据晚落盘(akshare当日bar收盘后生成)由调用方重试。--force重抓。"""
    import ashare_stock_pipeline as pipe          # 冻结文件: 只读调用, 不改写其CSV
    os.makedirs(ING_DIR, exist_ok=True)
    if not force:
        try:
            load_day(day)
            return json.load(open(os.path.join(ING_DIR, f"{day}.meta.json"), encoding="utf-8"))
        except Exception:                          # noqa: BLE001  缺文件/失配都走重抓(失配由重抓覆盖并留痕meta)
            pass
    day8 = day.replace("-", "")
    codes = codes or pipe.configured_stocks()
    rows, missing = [], []
    fetched_at = datetime_now_iso()
    for i, code in enumerate(codes):
        try:
            raw = pipe.fetch_one(code, start=day8, adjust="")
            raw = raw[raw["date"] == day] if len(raw) else raw
            if raw is None or not len(raw):
                missing.append({"code": code, "why": "no-bar(停牌/退市/新券)"})
                continue
            row = raw.iloc[-1]
            close_raw = float(row["close"])
            hf = pipe.fetch_one(code, start=day8, adjust="hfq")
            hf = hf[hf["date"] == day] if len(hf) else hf
            if not len(hf):
                missing.append({"code": code, "why": "hfq-empty"})
                factor = 1.0
            else:
                factor = float(hf.iloc[-1]["close"]) / close_raw if close_raw else 1.0
            rows.append({"date": day, "code": code, "sector": "",
                         "open": float(row["open"]), "high": float(row["high"]),
                         "low": float(row["low"]), "close": close_raw,
                         "volume": float(row["volume"]), "amount": float(row["amount"]),
                         "adj_factor": round(factor, 6), "close_adj": round(close_raw * factor, 4)})
        except Exception as e:                                    # noqa: BLE001  单券失败显式列缺, 不静默
            missing.append({"code": code, "why": f"{type(e).__name__}: {e}"[:120]})
        time.sleep(per_call_sleep)
        if (i + 1) % 50 == 0:
            print(f"[ingest] {i+1}/{len(codes)} ...", file=sys.stderr)
    csv_p = os.path.join(ING_DIR, f"{day}.csv")
    if not rows:
        raise RuntimeError(f"{day} 零成交行抓取, 前向数据不完整(禁止生成计划)")
    pd.DataFrame(rows).to_csv(csv_p, index=False, encoding="utf-8")
    meta = {"day": day, "fetched_at": fetched_at, "source": "akshare:stock_zh_a_hist(pipeline.fetch_one)",
            "rows": len(rows), "codes_requested": len(codes), "codes_missing": missing,
            "csv_sha256": _sha256(csv_p), "price_basis": "raw open/close(执行域)+close_adj(研究域)",
            "close_trading_after_fetch": fetched_at[11:16] >= "15:00:00"}
    with open(os.path.join(ING_DIR, f"{day}.meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return meta


def datetime_now_iso() -> str:
    return dt.datetime.now(TZ).isoformat()


def load_day(day: str) -> pd.DataFrame:
    """读增量日数据并审计: 缺文件/哈希失配→显式raise(P0-2负例)。"""
    csv_p = os.path.join(ING_DIR, f"{day}.csv")
    meta_p = os.path.join(ING_DIR, f"{day}.meta.json")
    if not os.path.exists(csv_p) or not os.path.exists(meta_p):
        raise FileNotFoundError(f"增量层无{day}数据/审计meta: 先 fetch --day {day}")
    meta = json.load(open(meta_p, encoding="utf-8"))
    if _sha256(csv_p) != meta["csv_sha256"]:
        raise ValueError(f"增量{day}.csv与meta哈希失配(数据可能被改写): {meta['csv_sha256']}")
    return pd.read_csv(csv_p, parse_dates=["date"])


def require_plan_ready(day: str) -> dict:
    """plan前校验: 当日收盘数据已抓齐且抓取时间≥15:00(收盘后才可信)。"""
    meta = json.load(open(os.path.join(ING_DIR, f"{day}.meta.json"), encoding="utf-8"))
    if not meta.get("close_trading_after_fetch"):
        raise RuntimeError(f"{day} 抓取于盘中({meta['fetched_at'][:16]}), 收盘bar未定, 禁止生成信号")
    return meta


def merge_with_frozen(portal_df: pd.DataFrame, days: list) -> pd.DataFrame:
    """冻结层(≤R31快照) + 增量层拼接; 增量不得覆盖冻结已有日期(冲突raise)。"""
    if not days:
        return portal_df
    add = pd.concat([load_day(d) for d in days], ignore_index=True)
    frozen_max = pd.Timestamp(portal_df["date"].max())
    clash = add[add["date"] <= frozen_max]["date"].unique()
    if len(clash):
        raise ValueError(f"增量层与冻结快照日期冲突(禁止覆盖历史): {list(map(str, clash))}")
    return pd.concat([portal_df, add], ignore_index=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_command = None
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--day", required=True)
    f.add_argument("--codes", default="")
    f.add_argument("--force", action="store_true", help="重抓覆盖(默认幂等复用当日已抓取)")
    a = sub.add_parser("audit")
    a.add_argument("--day", required=True)
    x = ap.parse_args()
    if x.cmd == "fetch":
        codes = x.codes.split(",") if x.codes else None
        print(json.dumps(fetch_day(x.day, codes, force=getattr(x, "force", False)), ensure_ascii=False))
    else:
        m = json.load(open(os.path.join(ING_DIR, f"{x.day}.meta.json"), encoding="utf-8"))
        df = load_day(x.day)
        print(json.dumps({"meta": m, "rows": len(df),
                          "null_key_cols": [c for c in ("open", "close", "adj_factor")
                                            if df[c].isna().any()]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
