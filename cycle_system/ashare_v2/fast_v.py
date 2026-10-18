# -*- coding: utf-8 -*-
"""fast_v — Fast-V 引擎: 急跌后快速反转 (阶段C交付, 完全独立)。

§3: 状态机 NO_SETUP/SHOCK_DETECTED/BOTTOM_SEARCH/REVERSAL_CANDIDATE/ENTRY_CONFIRMED/
REBOUND_ACTIVE/REVERSAL_FAILED/EXPIRED。
硬规则:
  - 与 Long-Cycle 互不为前置: 长期趋势弱也可独立发候选; 但不绕过停牌/重大风险/执行底线;
  - REVERSAL_CANDIDATE 只能由当日收盘可知的条件成立触发——禁止事后用最低价触发;
  - 退出=反弹失败/修复衰竭/趋势重新转弱/风险失效; EXPIRED 是本引擎研究窗口衰减,
    严禁移植到 Long-Cycle;
  - 同股双引擎: 两套理由都记录, 目标仓位由 signal_merge/portfolio 统一决定, 不重复占资金。
"""
from __future__ import annotations

from cycle_system.ashare_v2.schemas import FastVState, OpportunityRecord  # noqa: F401


class FastVEngine:
    def update_state(self, code: str, day: str) -> FastVState:
        raise NotImplementedError("阶段C")

    def candidates(self, day: str) -> list:
        """-> List[OpportunityRecord](engine=FAST_V)。"""
        raise NotImplementedError("阶段C")
