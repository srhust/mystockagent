# -*- coding: utf-8 -*-
"""universe — 历史股票池分层重建 (阶段B交付)。

§7.1: 复用 Baostock 名录(stock_universe_full.json, 含 ipo/out/type/status)+ 已有审计
(data_trust_report/stock_universe_rebuild)。输出按日 bucket 链:
LISTED_BEFORE→SURVIVING→HAS_QUOTES→TRADEABLE(复用 pool_at, 结果须 copy 后使用)→
ST_FLAG(UNVERIFIED)→LIQUIDITY→DATA_ENOUGH。
红线: 禁止以当前 249 配置池宣称已消除幸存者偏差; 用当前池的分析一律携带
SURVIVORSHIP_BIAS_PARTIAL 标注。注意配置表 251 条含重复 code(000703/600426), 先再去重。
"""
from __future__ import annotations


class UniverseProvider:
    def buckets_at(self, day: str) -> dict:
        """当日分层结果 + 每层计数 + 数据可信度标注。缺数据→DATA_BLOCKED, 不猜。"""
        raise NotImplementedError("阶段B")

    def survivorship_note(self) -> str:
        raise NotImplementedError("阶段B")
