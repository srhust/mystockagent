# -*- coding: utf-8 -*-
"""signal_merge — 双引擎候选合并与冲突处理 (阶段D交付)。

§3.4/§6: 两引擎候选同股时 → 保留两套理由(evidence 各自完整), 合并为单一目标持仓意向交
allocator; 不重复占用资金。冲突处理输出必须可追溯: 哪个引擎先/哪个主导/合并依据。
PIT 复检: 合并前逐条 OpportunityRecord.validate()——证据时点晚于信号日即整条拒绝并记
数据质量事件(§9.1 无未来信息泄漏, fail-closed)。
"""
from __future__ import annotations


class SignalMerge:
    def merge(self, lc_records: list, fv_records: list, day: str) -> list:
        """-> 合并后的合格机会(含同股双引擎标记); 不合格者进 data_quality 清单。"""
        raise NotImplementedError("阶段D")
