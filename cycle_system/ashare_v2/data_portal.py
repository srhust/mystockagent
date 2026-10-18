# -*- coding: utf-8 -*-
"""data_portal — 统一数据访问 + PIT 闸门 + 快照哈希 (阶段B实装)。

边界: 本包唯一允许触达底层数据的门面。
  - 价格面板: data/stocks/*.csv, strict 模式先逐文件 sha256 对 R31 manifest 锚验证, 不一致
    直接拒绝(fail-closed, §9.1"无数据Fail-Closed");
  - 基本面适配器: §7.3 十字段强制; PIT 规则 first_public_ts ≤ asof 才可见; 缺 first_public_ts
    → UNKNOWN 拒绝使用, 不回填; 当前外部指标零接入 → 一律 DATA_BLOCKED(不编造);
  - 日历: 复用 ashare_calendar, UNKNOWN 日期透传 RuntimeError(fail-closed)。
已知缺口显式携带(不静默): 历史ST不可得(ST_FLAG_UNVERIFIED) / 停牌以缺行近似 /
Ledger 语义缺口在 portfolio 层处理, 不在本层。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional

from cycle_system.ashare_v2 import _path  # noqa: F401  路径引导(先于旧模块导入)
from cycle_system.ashare_v2 import config as C
from cycle_system.ashare_v2.schemas import Availability, DataMode

import ashare_stock_pipeline as sp  # noqa: E402  只读复用(数据层)

MANIFEST_P = os.path.join(C.REPO_ROOT, "output", "ashare", "manifest.json")

INDICATOR_CATALOG: Dict[str, dict] = {}
for _ind, _items in {
    "生猪养殖": ["pig.price", "pig.sow_stock", "pig.cost", "pig.margin"],
    "工业金属(铜)": ["cu.supply_demand", "cu.inventory", "cu.capacity", "cu.tc_rc", "cu.price"],
    "黄金": ["au.real_rate", "au.price", "au.output", "au.cost"],
    "煤炭": ["coal.price", "coal.inventory", "coal.output", "coal.demand", "coal.profit_capex"],
    "工程机械": ["cm.sales", "cm.operating_rate", "cm.renewal", "cm.inventory", "cm.profit"],
    "半导体": ["semi.inventory", "semi.orders", "semi.price", "semi.capex", "semi.profit"],
    "银行": ["bank.nim", "bank.credit", "bank.asset_quality"],
    "证券": ["brk.turnover", "brk.margin", "brk.ib", "brk.profit"],
    "保险": ["ins.rate", "ins.equity", "ins.income"],
    "成长景气": ["growth.demand", "growth.orders", "growth.rev_momentum"],
}.items():
    for _k in _items:
        INDICATOR_CATALOG[_k] = {"industry": _ind, "status": Availability.DATA_BLOCKED.value}


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class DataIntegrityError(RuntimeError):
    """strict 模式数据锚不一致 → 拒绝一切正式信号。"""


class DataPortal:
    def __init__(self, data_mode: str = DataMode.STRICT.value, verify_hashes: bool = True):
        if data_mode not in (DataMode.STRICT.value, DataMode.RESEARCH.value):
            raise ValueError(f"未知 data_mode: {data_mode}")
        self.data_mode = data_mode
        self.verify_hashes = verify_hashes
        self._panel = None
        self._fund: List[dict] = []
        self._annos: List[str] = []

    # ── 快照/锚 ──
    def load_manifest(self) -> dict:
        return json.load(open(MANIFEST_P, encoding="utf-8"))

    def snapshot_hash(self) -> dict:
        man = self.load_manifest()
        import ashare_calendar as cal  # noqa: E402
        return {
            "manifest_sha256": sha256_file(MANIFEST_P),
            "manifest_git_sha": man.get("git_sha"),
            "n_csv": man.get("n_csv"),
            "calendar_csv_sha256": sha256_file(cal.CACHE_P) if os.path.exists(cal.CACHE_P) else None,
            "stocks_dir_sha256_of_concat": hashlib.sha256(
                "|".join(f"{k}:{v}" for k, v in sorted(man["csv_hashes"].items())).encode()).hexdigest(),
        }

    def _verify_csvs(self, man: dict) -> None:
        bad = []
        for name, want in man["csv_hashes"].items():
            p = os.path.join(C.STOCKS_DIR, name)
            if not os.path.exists(p):
                bad.append(f"缺失 {name}")
            elif sha256_file(p) != want:
                bad.append(f"哈希失配 {name}")
        if bad:
            raise DataIntegrityError("strict模式数据锚失败(fail-closed): " + "; ".join(bad[:5]))

    # ── 价格面板(仅CSV可知字段; 不加任何未来信息) ──
    def price_panel(self):
        import pandas as pd
        if self._panel is not None:
            return self._panel
        man = self.load_manifest()
        if self.verify_hashes:
            self._verify_csvs(man)
        elif self.data_mode == DataMode.STRICT.value:
            raise DataIntegrityError("strict模式禁止关闭哈希校验")
        rows = []
        seen = set()
        pool = []
        for code, name, sector in sp.configured_stocks():  # 251条含重复000703/600426 → 去重
            if code in seen:
                continue
            seen.add(code)
            pool.append((code, name, sector))
        sector_of = dict((c, s) for c, _, s in pool)
        for f in sorted(os.listdir(C.STOCKS_DIR)):
            if not f.endswith(".csv"):
                continue
            code = f[:-4]
            p = os.path.join(C.STOCKS_DIR, f)
            df = pd.read_csv(p)
            if "adj_factor" not in df.columns:
                continue
            df["code"] = code
            df["sector"] = sector_of.get(code, "")  # 603603不在配置表: 保留但sector空→行业层排除并标注
            df["close_adj"] = df["close"] * df["adj_factor"]
            rows.append(df[["date", "code", "sector", "open", "high", "low", "close", "volume", "amount",
                            "adj_factor", "close_adj"]])
        panel = pd.concat(rows).drop_duplicates(subset=["date", "code"], keep="first")
        panel["date"] = pd.to_datetime(panel["date"])
        self._panel = panel.sort_values(["date", "code"]).reset_index(drop=True)
        return self._panel

    # ── 基本面适配器(PIT 闸门核心) ──
    def register_fundamental(self, rec: dict) -> None:
        """§7.3 十字段强制。缺任一→拒绝入册(不静默)。"""
        need = ["indicator_id", "industry", "period", "value", "source", "first_public_ts",
                "effective_ts", "fetched_ts", "revision", "completeness"]
        miss = [k for k in need if k not in rec or rec[k] in (None, "")]
        if miss:
            rec = dict(rec)
            rec["_reject_reason"] = f"缺字段{miss}→UNKNOWN不回填"
            rec["availability"] = Availability.UNKNOWN.value
            self._annos.append(f"基本面记录被拒(缺{miss[0]}): {rec.get('indicator_id')}")
            self._fund.append(rec)   # 仅留审计痕迹, pit_records 永不可见
            return
        rec = dict(rec)
        rec["availability"] = Availability.CONFIRMED.value if rec["completeness"] == "COMPLETE" \
            else Availability.DATA_LIMITED.value
        self._fund.append(rec)

    def pit_records(self, indicator_id: str, asof: str) -> dict:
        """返回 asof 收盘时点已公开(first_public_ts≤asof)的记录。"""
        vis = [r for r in self._fund
               if r.get("indicator_id") == indicator_id
               and r.get("availability") != Availability.UNKNOWN.value
               and str(r.get("first_public_ts", ""))[:10] <= asof]
        blocked = indicator_id in INDICATOR_CATALOG and not any(
            r.get("indicator_id") == indicator_id and r.get("availability") != Availability.UNKNOWN.value
            for r in self._fund)
        if not vis:
            status = (Availability.DATA_BLOCKED.value if blocked else Availability.UNKNOWN.value)
            if blocked and self.data_mode == DataMode.STRICT.value:
                return {"status": status, "records": [],
                        "strict_effect": "拒绝基于该指标产生正式交易信号(§7.4)"}
            return {"status": status if blocked else Availability.DATA_LIMITED.value, "records": []}
        return {"status": "OK", "records": vis}

    # ── 日历 ──
    def is_trading_day(self, day: str) -> bool:
        import ashare_calendar as cal  # noqa: E402
        return cal.is_trading_day(day)  # UNKNOWN→RuntimeError 透传(fail-closed)

    # ── 质量报告(§10 data_quality_report.json) ──
    def quality_report(self) -> dict:
        return {
            "mode": self.data_mode,
            "snapshots": self.snapshot_hash(),
            "datasets": {
                "price_panel_csv": {"status": "CONFIRMED", "note": "250 CSV sha256↔R31 manifest逐文件验证"
                                                      if self.verify_hashes else "RESEARCH: 未逐文件验证(显式降级)"},
                "market_calendar": {"status": "CONFIRMED", "range": "2012-06-08..2026-10-13(并集); 官方日程至2026-12-31",
                                     "fail_closed": "超出→RuntimeError"},
                "tradeable_pool": {"status": "RECONSTRUCTABLE", "source": "ashare_tradeable_pool.pool_at(九桶)",
                                    "gaps": ["SUSPENDED_CONFIRMED永不产出(无权威停复牌)", "ST历史UNAVAILABLE"]},
                "universe_marketwide": {"status": "DATA_BLOCKED", "reason": "Baostock 9011全名录未落盘(R48 DATA_NOT_CACHED)"
                                          , "fallback": "227退市清单+81月存续曲线可用"},
                "industry_sw_historical": {"status": "PIT_UNVERIFIED", "reason": "申万历史成员verified=0(R44-48), in_date部分覆盖(5222码)但out缺失"},
                "industry_snapshot": {"status": "PROXY_ONLY", "source": "CYCLE_SECTORS当前快照(30板块249码)"},
                "fundamental_indicators": {k: v["status"] for k, v in INDICATOR_CATALOG.items()},
            },
            "explicit_annotations": self._annos + [
                "SURVIVORSHIP_BIAS_PARTIAL: 250池=当前配置池, 未消除幸存者偏差",
                "ST_FLAG_UNVERIFIED; suspension=缺行近似",
                "2020-2026为研究窗口, 非样本外"],
        }
