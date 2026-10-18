# -*- coding: utf-8 -*-
"""opportunity_ranker — 可解释机会评分 (阶段D交付)。

§4/§5.3: 分项线性式(config.SCORE_WEIGHTS, 预置未校准): 周期阶段质量/长窗口证据/相对强弱/
流动性可成交/数据可信度。要求:
  - 输出逐分项贡献(score_breakdown), "为何交易/为何不交易"必须可读;
  - 不得只按 20 日动量排名; CALIBRATED=False 期间产出禁用"预期收益率/胜率"字样(测试检查);
  - 评分服务于统一风险尺度排序, 不是收益预测。
"""
from __future__ import annotations


class OpportunityRanker:
    def rank(self, opportunities: list, risk_ctx: dict) -> list:
        """附 score/score_breakdown/decision+理由; 数据可信度 DATA_BLOCKED→直接 NO_TRADE。"""
        raise NotImplementedError("阶段D")
