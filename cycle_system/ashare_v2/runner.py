# -*- coding: utf-8 -*-
"""runner — 三模式入口 (阶段F交付)。

§10/§11: diagnostic(只读, 不成交) / synthetic(合成+冻结数据, 非样本外) / shadow(收盘计划→
次日真实约束模拟, 全量记录 计划/拒单/成交/持仓/现金/NAV/数据快照)。
三模式共享同一信号与资金代码路径, 差异仅是执行副作用开关。
每次运行写 output/ashare_v2/ 全部 §10 产物 + experiment_manifest.json(代码版本/配置哈希/
数据哈希/信息时点)。日历超出已确认范围→fail-closed 拒绝, 不猜交易日。
"""
from __future__ import annotations

MODES = ("diagnostic", "synthetic", "shadow")


def main(argv=None) -> int:
    raise NotImplementedError("阶段F")
