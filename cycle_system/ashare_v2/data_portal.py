# -*- coding: utf-8 -*-
"""data_portal — 统一数据访问 + PIT 闸门 + 数据快照 (阶段B交付)。

职责(DATA_CONTRACT.md §1/§4/§5/§7):
  - 唯一允许触达 CSV/日历/池/外部源的出口; 每次读取记录 fetched_ts+来源哈希;
  - PIT 闸门: 指标仅当 first_public_ts ≤ 信号日收盘可用; 缺失→UNKNOWN 不回填;
  - 输出 data_quality_report.json: 逐数据集 CONFIRMED/RECONSTRUCTABLE/PROXY_ONLY/
    DATA_LIMITED/DATA_BLOCKED/PIT_UNVERIFIED。
已知缺口(阶段B必须显式携带): Ledger 无回放; nav 缺价"last"未实现→本层自补估值;
日历上限 2026-12-31; 申万历史成员 UNKNOWN; 历史ST/权威停复牌不可得。
"""
from __future__ import annotations


class DataPortal:
    """价格面板/日历/可交易池/基本面的只读门面。strict 模式关键缺失即拒绝出信号。"""

    def price_panel(self):                      # -> pd.DataFrame (date,code,sector,...,close_raw,close_adj,...)
        raise NotImplementedError("阶段B")

    def fundamental(self, indicator_id: str, asof: str):
        """返回 PIT 过滤后的指标记录集; 无发布时间戳的记录不得进入返回集。"""
        raise NotImplementedError("阶段B")

    def snapshot_hash(self) -> dict:
        raise NotImplementedError("阶段B")
