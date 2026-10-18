# -*- coding: utf-8 -*-
"""capital_allocator — 统一资金竞争贪心分配 (阶段D交付)。

§5: 接收双引擎全部合格机会; config.ENGINE_SPLIT_FRAC/MAX_POSITIONS 恒为 None(测试断言
allocator 不存在固定引擎仓位/固定持仓数常量)。流程(§5.3): 统一尺度→合并高重叠→
(持仓,新机会,现金,相关性)四视角→风险调整边际价值排序→贪心→每笔重算剩余风险预算→
无可信正边际即停→目标持仓+未分配现金。
集中度边界(§5.2): 无好机会→全现金; 单优势行业可 60%+; 双行业集中; 多低相关适度分散。
风险约束(§5.4): 相关性/流动性/波动尾部/压力情景/T+1/涨跌停不可成交/重叠/费用预留/换仓成本;
无杠杆。换仓纪律(§5.5): 最小优势差距/成本比较/原逻辑成立性/预算变化/换手抑制,
阈值=config 预置常数(不得按旧样本收益寻优)。
"""
from __future__ import annotations


class GreedyAllocator:
    def allocate(self, ranked_opps: list, portfolio_ctx: dict, risk_state: dict) -> dict:
        """-> {"targets":[...], "unallocated_cash":..., "stops":[逐笔停止原因]}"""
        raise NotImplementedError("阶段D")
