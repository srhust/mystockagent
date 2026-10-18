# -*- coding: utf-8 -*-
"""industry_cycle — 行业分类 Provider + 周期阶段判定 (阶段B交付)。

§7.2 信任分层(强制, 见 schemas.MembershipTrust):
  SW_PIT_VERIFIED / SW_PARTIAL / CSRC_HISTORICAL_DATE / CURRENT_SNAPSHOT_ONLY / UNKNOWN。
当前实际: 申万二级历史成员=UNKNOWN; 证监会口径=CSRC_HISTORICAL_DATE; 池 sector 列=
CURRENT_SNAPSHOT_ONLY(代理)。Strict 模式行业级正式信号→DATA_BLOCKED; Research 允许代理但
逐条 PROXY_ONLY。2014/2021 版本显式区分; 语义不同的行业指数禁止拼接。
阶段判定输入为 B 层市场表现特征(120/250/500 区间位置/60-120-250 趋势/6-12-24 月动量/
相对强度/回撤与波动)→输出 §2.3 状态机迁移, 全部窗口只用 T 日及以前数据。
"""
from __future__ import annotations

from cycle_system.ashare_v2.schemas import LongCycleState  # noqa: F401  契约引用


class IndustryMembershipProvider:
    def sector_of(self, code: str, day: str) -> dict:
        """-> {"industry":…, "taxonomy_version":…, "trust":…, "asof":…}  trust 永不缺省。"""
        raise NotImplementedError("阶段B")


class IndustrySeriesBuilder:
    def index_for(self, industry: str, day: str):
        """按时点成员构造行业序列; 成员 trust 不足时返回 PROXY/DATA_BLOCKED 而非静默快照。"""
        raise NotImplementedError("阶段B")

    def stage(self, industry: str, day: str) -> LongCycleState:
        raise NotImplementedError("阶段B")
