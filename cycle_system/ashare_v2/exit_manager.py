# -*- coding: utf-8 -*-
"""exit_manager — 双引擎退出分级 WARNING/REDUCE/EXIT (阶段C交付框架, D接风险预算)。

Long-Cycle(§2.5): 底部假设被否定 / 中长趋势实质破坏 / 个股持续显著弱于行业 / 公司经营风险 /
风险预算触发 / 更优机会满足换仓条件 → EXIT/REDUCE; MA20 破位仅 WARNING。
不存在基于持仓天数的退出分支(工程测试直接检查本模块 Long-Cycle 路径无天数条件)。
Fast-V(§3.5): 反弹失败/修复衰竭/趋势重新转弱/风险失效 → EXIT; EXPIRED 走引擎自身衰减语义。
每次退出指明所属虚拟头寸与触发条款, 供归因。
"""
from __future__ import annotations

from cycle_system.ashare_v2.schemas import ExitClass  # noqa: F401


class ExitManager:
    def evaluate(self, position, day: str) -> list:
        """-> [(ExitClass, reason_clause, affected_position_ids)]"""
        raise NotImplementedError("阶段C/D")
