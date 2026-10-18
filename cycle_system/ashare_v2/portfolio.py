# -*- coding: utf-8 -*-
"""portfolio — 双引擎虚拟持仓 + PortfolioCoordinator 真实净目标 (阶段E交付)。

§6: 双引擎各持虚拟账(来源引擎+初始判断+归因), 真实组合共享单一 `ashare_executor.Ledger`
(只读复用, 不修改执行器)。规则:
  - 同股不重复承诺资金(合并净目标); 卖出指明虚拟头寸 id;
  - 风险强制退出可覆盖引擎决策; 一般 WARNING 不清除另一引擎持仓;
  - 卖出优先于依赖该资金的新增买入(NetTarget.sells 先于 buys);
  - 不重复登记 SELL/重复扣费(plan_hash 幂等 + Ledger A3 重复拒绝, 双保险);
  - T+1/整手/涨跌停/停牌判定全部走执行器, V2 不自行放宽。
Ledger 缺口自补: 重启回放由本层重建虚拟账(账本 JSONL 只写不读→本层维护可重放快照),
缺价估值用"最近有效价"并标注(执行器 nav(missing_action) 未实现该语义)。
"""
from __future__ import annotations

from cycle_system.ashare_v2.schemas import NetTarget, VirtualPosition  # noqa: F401


class PortfolioCoordinator:
    def sync_virtuals(self, fills: list, day: str) -> None:
        raise NotImplementedError("阶段E")

    def net_targets(self, alloc_result: dict, exits: list, day: str) -> dict:
        """-> NetTarget(卖先买后/合并承诺/风险覆盖标记)。"""
        raise NotImplementedError("阶段E")
