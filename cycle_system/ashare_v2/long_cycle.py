# -*- coding: utf-8 -*-
"""long_cycle — 主引擎: 产业周期反转状态机 (阶段C交付)。

§2: 三层信息(A 基本面适配器[B 行业表现][C 个股确认)→ §2.3 状态机
(DOWNTREND/BOTTOM_WATCH/BOTTOM_FORMING/EARLY_RECOVERY/EXPANSION/TREND_IMPAIRED/
INVALIDATED/UNKNOWN)。
硬规则:
  - 迁移只用当时信息; 禁止用未来最低点标注历史底部;
  - 三类入口(EARLY_REVERSAL/BREAKOUT_CONFIRM/TREND_CONTINUATION)共用同一状态机;
  - 退出禁止固定持仓天数(本模块不存在任何"持有N天"分支);
  - MA20 破位只能产生 WARNING(exit_manager 分级), 不构成 EXIT。
适配器现状: 外部产业指标未接入→相应判定降级标注(PROXY_ONLY/DATA_BLOCKED), 见 DATA_CONTRACT §4。
"""
from __future__ import annotations

from cycle_system.ashare_v2.schemas import LongCycleState, OpportunityRecord  # noqa: F401


class LongCycleEngine:
    def update_state(self, industry: str, day: str) -> LongCycleState:
        raise NotImplementedError("阶段C")

    def candidates(self, day: str) -> list:
        """-> List[OpportunityRecord](engine=LONG_CYCLE), validate() 通过才可返回。"""
        raise NotImplementedError("阶段C")
