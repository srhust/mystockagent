# -*- coding: utf-8 -*-
"""reversal_detector.py (P0): 三路径周期启动候选生成 (CycleStart-v1)。
路径A 深跌V型: 252日回撤>25% + 下跌动量改善; 触发=站上MA20 + 20日RS转正 + 广度回升。
路径B 长期U型: 曾明显下跌 + 40-60日波动收窄不再创新低; 触发=破20日高 + MA20斜率转正 + 广度扩散。
路径C 浅回撤启动: 回撤8%~25% + 长期RS未恶化; 触发=破短期平台 + RS与广度同步改善。
快速通道: 深跌+放量+广度急升 -> 跳过完整筑底确认。
等级: watch(观察) < early(两项改善) < start(确认)。"""
import numpy as np
import pandas as pd

G, R, HORIZON = 0.15, 0.10, 120


def detect(close, ref, breadth=None, volume=None):
    """close/ref: 行业指数与基准(沪深300)收盘; breadth: 行业广度序列(可选); 返回0/1信号帧。"""
    dd252 = close / close.rolling(252, min_periods=120).max() - 1.0
    ma20 = close.rolling(20).mean()
    rs = close / ref.reindex(close.index)
    rs20 = rs.pct_change(20)
    rs_long = rs / rs.rolling(252, min_periods=120).mean() - 1.0
    bd = (breadth - breadth.shift(20)) if breadth is not None else pd.Series(0.0, index=close.index)
    mom20 = close.pct_change(20)
    mom_improve = mom20 > mom20.shift(10)
    vol_narrow = close.pct_change().rolling(40).std() < close.pct_change().rolling(120).std() * 0.9
    no_new_low = close > close.rolling(40).min() * 1.01
    ma20_slope = ma20 > ma20.shift(5)
    brk20 = close > close.rolling(20).max().shift(1)
    vol_surge = pd.Series(False, index=close.index)
    if volume is not None:
        vol_surge = volume.rolling(20).mean() > volume.rolling(60).mean() * 1.8

    watch_A = dd252 <= -0.25
    watch_B = (dd252 <= -0.25) & vol_narrow & no_new_low
    watch_C = (dd252 > -0.25) & (dd252 <= -0.08) & (rs_long > -0.15)

    improve = (mom_improve.astype(int) + (rs20 > 0).astype(int) + (bd > 0).astype(int))
    early = ((watch_A | watch_B | watch_C) & (improve >= 2)).astype(int)
    start = ((watch_A & (close > ma20) & (rs20 > 0) & (bd > 0.02))
             | (watch_B & brk20 & ma20_slope & (bd > 0))
             | (watch_C & brk20 & (rs20 > 0) & (bd > 0))
             | (watch_A & vol_surge & (bd > 0.05))).astype(int)  # 快速通道含末项
    watch = (watch_A | watch_B | watch_C).astype(int)
    return pd.DataFrame({"watch": watch, "early": early, "start": start,
                         "pathA": (start & watch_A).astype(int), "pathB": (start & watch_B & ~watch_A).astype(int),
                         "pathC": (start & watch_C).astype(int), "rs20": rs20, "bd": bd})


def effective_start(close, *, g=0.15, r=0.10, horizon=HORIZON, vol_win=250):
    """有效启动标签(信号日用): t后horizon日内先涨g×s先于先跌-r×s => 1; 反之0; 未触发nan。
    s=clip(0.20/vol250, 0.5, 2.0)。"""
    c = close.values
    n = len(c)
    vol = close.pct_change().rolling(vol_win).std().values
    s = np.clip(0.20 / np.where(vol > 0, vol, np.nan), 0.5, 2.0)
    lab = np.full(n, np.nan)
    for t in range(n - horizon):
        if not np.isfinite(s[t]):
            continue
        w = c[t + 1:t + 1 + horizon]
        fmax, fmin = w.max() / c[t] - 1, w.min() / c[t] - 1
        gg, rr = g * s[t], r * s[t]
        if fmax >= gg and fmin > -rr:
            lab[t] = 1
        elif fmin <= -rr and fmax < gg:
            lab[t] = 0
    return pd.Series(lab, index=close.index)
