# -*- coding: utf-8 -*-
"""capital_allocator.py (CycleTrend-v1 Phase5): 机会驱动的动态资金分配, 与买卖点信号研究解耦。
输入: cash, positions, candidates(各行业T-1可得特征), risk caps。
评分OpportunityScore(可解释, 全部已验证组件): 深度分(dd/自适应thr) + 低温分(temp) — 越深越冷越高。
分配: score加权 × 上限cap_conc, 无机会时持现金; 邻近机会(watch)时预留reserve。
输出: 各候选的target_budget。禁止使用未来周期起点。"""
import numpy as np
import pandas as pd

CAP_CONC = 0.35   # 单行业上限(占当时总权益)
RESERVE = 0.20    # 存在观察态行业时现金预留
WATCH_TEMP = 25.0


def opportunity_score(temp, dd, thr):
    """T-1日可得: temp_v4, dd, 自适应thr。分数∈[0,1]。"""
    depth = min(1.0, abs(dd) / max(thr, 1e-6))
    cold = max(0.0, (WATCH_TEMP - temp) / WATCH_TEMP) if temp < WATCH_TEMP else 0.0
    return float(np.clip(0.5 * depth + 0.5 * cold, 0.0, 1.0))


def allocate(candidates, n_watch, equity, cash):
    """candidates: {sector: score}; 返回 {sector: target_budget}。score加权+集中度上限+观察预留。"""
    if not candidates:
        return {}
    usable = equity * (1 - (RESERVE if n_watch > len(candidates) else 0.0))
    usable = min(usable, cash)
    tot = sum(candidates.values())
    out = {}
    for s, sc in candidates.items():
        w = sc / tot if tot > 0 else 0.0
        out[s] = min(usable * w, equity * CAP_CONC)
    return out
