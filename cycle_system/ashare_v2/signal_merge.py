# -*- coding: utf-8 -*-
"""signal_merge — 双引擎候选统一(阶段D实装)。

§1.2/§6: 统一资金竞争——本层**不做优先级、不做抑制、不做固定引擎仓位**:
  * 同股两引擎各出记录 → 归因保留两条, 资金行合并为一条(资金只承诺一次, §6.2);
  * 引擎占比是评分×风险的结果, 不是约束(测试锁: 高分的单引擎可拿全部资金);
  * 每行携带 calibration_note: 评分未校准, 禁止收益/胜率解读。
"""
from __future__ import annotations

from __future__ import annotations

from cycle_system.ashare_v2 import config as C


def merge_candidates(lc_records: list, fv_records: list, age_days: dict | None = None) -> list:
    age_days = age_days or {}
    by_code: dict = {}
    for rec in list(lc_records) + list(fv_records):
        slot = by_code.setdefault(rec.code, {"code": rec.code, "records": []})
        slot["records"].append(rec)
    out = []
    for code, slot in by_code.items():
        reps = slot["records"]
        out.append({
            "code": code,
            "records": reps,
            "engines": sorted({r.engine for r in reps}),
            "age_days": age_days.get(code, {}),
            "merge_note": ("同股双引擎: 归因记录两条, 资金承诺一条(§6.2)" if len(reps) > 1 else ""),
            "calibration_note": "评分未校准(CALIBRATED=False), 本列表非收益/胜率排序声明",
        })
    return out
