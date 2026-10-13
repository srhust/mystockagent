# -*- coding: utf-8 -*-
"""breakout_detector.py (CycleTrend-v1 Phase2, Engine B): 突破启动。
严格只用已完成K线: close[t] > max(close[t-20:t]) 等价 rolling(N).max().shift(1)。
信号: brk20/brk55/brk120 Donchian突破; 平台突破(120日窄幅后突破); 波动压缩后突破。
确认失败: 突破后20日内收盘跌回突破日价位以下 -> BREAKOUT_FAILED。
确认叠加(分步检验, 不默认全开): rs20>0 / 广度 / 成交额放大。"""
import numpy as np
import pandas as pd


def detect_breakout(close, ref=None, breadth=None, amount=None, *, windows=(20, 55, 120), fail_n=20):
    out = pd.DataFrame(index=close.index)
    hi_prev = {n: close.rolling(n).max().shift(1) for n in windows}
    for n in windows:
        out[f"brk{n}"] = (close > hi_prev[n]).astype(float)
    # 平台突破: 突破前60日区间宽度<15%
    rng = (close.rolling(60).max() / close.rolling(60).min() - 1.0)
    out["platform"] = ((close > hi_prev[120]) & (rng.shift(1) < 0.15)).astype(float)
    # 波动压缩后突破
    volc = close.pct_change().rolling(20).std() < close.pct_change().rolling(120).std() * 0.8
    out["volc_brk"] = ((close > hi_prev[55]) & volc.shift(1)).astype(float)
    # 突破失败: 突破后fail_n日内收盘 < 突破日收盘×0.97
    failed = pd.Series(0.0, index=close.index)
    brk_any = out["brk55"] > 0
    idx_brk = np.where(brk_any)[0]
    cv = close.values
    for i in idx_brk:
        if i + 1 < len(cv):
            lo = cv[i] * 0.97
            seg = cv[i + 1:min(i + 1 + fail_n, len(cv))]
            if np.nanmin(seg) < lo:
                failed.iloc[i] = 1.0
    out["brk55_failed"] = failed
    # 穿越去重(审查: 连续新高日不应重复入场): 首次突破 + 突破前20日曾有≥5%回撤(真平台/回调后启动)
    brk55 = out["brk55"] > 0
    cross = brk55 & ~brk55.shift(1).fillna(False).astype(bool)
    pullback = close.rolling(20).min() < hi_prev[55] * 0.95
    out["brk55_x"] = (cross & pullback).astype(float)
    # 确认层(分步)
    if ref is not None:
        rs = close / ref.reindex(close.index)
        out["rs20_pos"] = (rs.pct_change(20) > 0).astype(float)
        out["brk55_rs"] = (out["brk55"] * out["rs20_pos"]).astype(float)
    if breadth is not None:
        out["brk55_bd"] = (out["brk55"] * ((breadth - breadth.shift(20)) > 0).astype(float)).astype(float)
    if amount is not None:
        out["brk55_vol"] = (out["brk55"] * (amount.rolling(20).mean() > amount.rolling(60).mean() * 1.2).astype(float)).astype(float)
    return out
