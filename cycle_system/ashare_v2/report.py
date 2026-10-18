# -*- coding: utf-8 -*-
"""report — §10 产物与溯源封装 (阶段F交付)。

职责: 组装 schemas.Provenance; 写出 cycle_state_daily.json / *_candidates.json /
opportunity_ranking.json / capital_allocation.json / trade_plans.json / risk_report.json /
daily_cycle_report.md / data_quality_report.json / experiment_manifest.json;
校验每份产物含溯源头与显式 PIT 标注; 措辞纪律(CALIBRATED=False→禁"预期收益率/胜率")。
"""
from __future__ import annotations


def emit(out_dir: str, payload: dict, provenance: dict) -> list:
    """写全套产物, 返回文件清单; 任何缺溯源头→拒绝写出并报错(fail-closed)。"""
    raise NotImplementedError("阶段F")
