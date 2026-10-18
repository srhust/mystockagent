# -*- coding: utf-8 -*-
"""universe — 历史股票池分层 (阶段B实装, 能力边界如实标注)。

可用(离线已落盘):
  * pool_at(day) 九桶(当前配置池口径, 复用冻结 ashare_tradeable_pool——返回共享引用, 本层 copy);
  * 227 条全市场退市清单(stock_universe_full.json, out 日期, R46/R48 规范化哈希);
  * 81 个月度上市存续计数曲线(stock_universe_rebuild.json, 2020-01..2026-09);
  * 每只 CSV 首末行情日(数据内可知)。
不可用(不编造):
  * 全市场 9011 名录未落盘(R48 DATA_NOT_CACHED)→逐股 MARKET_WIDE 分层 DATA_BLOCKED, 只有聚合计数;
  * 历史实际可交易池 NOT_VERIFIED(停牌/ST权威记录缺)。
红线: 禁止以当前池宣称已消除幸存者偏差——所有基于本池的正式研究携带
SURVIVORSHIP_BIAS_PARTIAL。
"""
from __future__ import annotations

import json
import os
from copy import deepcopy

from cycle_system.ashare_v2 import _path  # noqa: F401,E402  必须先于旧模块导入(路径引导)
from cycle_system.ashare_v2 import config as C
from cycle_system.ashare_v2.schemas import Availability

import ashare_tradeable_pool as tp  # noqa: E402

_FULL = json.load(open(os.path.join(C.REPO_ROOT, "output", "ashare", "stock_universe_full.json"), encoding="utf-8"))
_REB = json.load(open(os.path.join(C.REPO_ROOT, "output", "ashare", "stock_universe_rebuild.json"), encoding="utf-8"))

DELISTED_MARKET_WIDE = {r["code"][-6:]: r for r in _FULL["delisted_full"]}          # 6位码→记录(out)
FUTURE_OUTDATE = {c: r for c, r in DELISTED_MARKET_WIDE.items() if r["out"] > "2026-10-18"}
MONTHLY_ALIVE = {row["month"]: row["n_alive"] for row in _REB["monthly_alive_curve"]}
STATUS_SIX = _FULL["status_table"]
SURVIVORSHIP_NOTE = ("SURVIVORSHIP_BIAS_PARTIAL: 250文件=当前配置池(249唯一码+603603), "
                     "全市场历史池仅聚合曲线可用(名录DATA_BLOCKED), 未消除幸存者偏差")


class UniverseProvider:
    def __init__(self, portal):
        self.portal = portal
        self._first_last = None

    def _code_first_last(self):
        if self._first_last is None:
            panel = self.portal.price_panel()
            g = panel.groupby("code")["date"]
            self._first_last = {c: (str(a.min().date()), str(a.max().date())) for c, a in g}
        return self._first_last

    def buckets_at(self, day: str) -> dict:
        """当前池九桶(copy隔离) + 退市到日 + 数据内可知层 + 市场-wide DATA_BLOCKED 声明。"""
        raw = tp.pool_at(day)                      # 共享引用→绝不原地修改
        buckets = deepcopy(raw["buckets"])
        fl = self._code_first_last()
        has_quotes = [c for c, (a, b) in fl.items() if a <= day <= b]
        no_quotes_yet = [c for c, (a, b) in fl.items() if day < a]
        after_last = [c for c, (a, b) in fl.items() if day > b]
        delisted_to_asof = {c: r for c, r in DELISTED_MARKET_WIDE.items() if r["out"] <= day}
        return {
            "day": day,
            "pool_current": buckets,
            "pool_stats": {k: len(v) for k, v in buckets.items()},
            "data_layers": {
                "HAS_QUOTES_T": has_quotes,
                "NO_QUOTES_YET(近似NOT_YET_LISTED, 仅数据内可知)": no_quotes_yet,
                "AFTER_LAST_QUOTE": after_last,
            },
            "market_wide": {
                "status": Availability.DATA_BLOCKED.value,
                "reason": "9011全名录未落盘(R48 DATA_NOT_CACHED); 逐股历史资格不可离线精确反推",
                "aggregate_only": {"month": day[:7], "n_alive": MONTHLY_ALIVE.get(day[:7]),
                                   "note": "月末存续计数≠可交易计数(停牌/ST/流动性未验证)"},
                "delisted_to_asof_n": len(delisted_to_asof),
            },
            "delisted_in_pool_after_last": [c for c in after_last if c in DELISTED_MARKET_WIDE],
            "caveats": {**raw.get("caveats", {}), "survivorship": SURVIVORSHIP_NOTE,
                        "sixfold": STATUS_SIX},
        }
