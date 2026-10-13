# -*- coding: utf-8 -*-
"""signal_engine: 三锚统一信号接口 —— 回测/每日建议/模拟盘共用同一套函数。
状态机: NORMAL → WATCH(深寒观察) → CANDIDATE(底部候选) → HOLD → TOP_WARN → EXIT
唯一事实来源; 任何脚本不得自行重写买卖条件。"""
from dataclasses import dataclass

import numpy as np
import pandas as pd


def temperature(close: pd.Series) -> pd.Series:
    """行业温度 0-100（与 cycle_system.strategy 完全同参）。"""
    lo = close.rolling(1250, min_periods=500).min()
    hi = close.rolling(1250, min_periods=500).max()
    pp = (close - lo) / (hi - lo)
    r120 = close.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = close.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)


def drawdown(close: pd.Series) -> pd.Series:
    return close / close.cummax() - 1.0


def macd_divergence(close: pd.Series, fast: int = 12, slow: int = 26) -> pd.DataFrame:
    """MACD 背离(墨派/波浪片段): 底背离=价格创60日新低而DIF未创新低; 顶背离反之。"""
    dif = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    lo60 = close.rolling(60, min_periods=30).min()
    hi60 = close.rolling(60, min_periods=30).max()
    dif_lo = dif.rolling(60, min_periods=30).min()
    dif_hi = dif.rolling(60, min_periods=30).max()
    bull = (close <= lo60 * 1.005) & (dif > dif_lo * 1.02)   # 底背离
    bear = (close >= hi60 * 0.995) & (dif < dif_hi * 0.98)   # 顶背离
    return pd.DataFrame({"dif": dif, "bull_div": bull, "bear_div": bear})


def adaptive_threshold(dd: pd.Series, floor: float = 0.30, k: float = 0.70,
                       win: int = 1250) -> pd.Series:
    """锚①: 阈值 = max(floor, k×近win日最深回撤) —— 每资产自身振幅。"""
    dep = (-dd).rolling(win, min_periods=250).max()
    return np.maximum(floor, k * dep)


def entry_signal(close: pd.Series, fut_pos: float = None, fut_max: float = 0.60,
                 temp_th: float = 12.0) -> pd.DataFrame:
    """统一入场: temp<temp_th 或 dd≤自适应T; 商品位置过滤(锚②)可选。
    返回 DataFrame[index] = {temp, dd, T, entry, reason}。"""
    t, dd = temperature(close), drawdown(close)
    dep = (-dd).rolling(1250, min_periods=250).max()
    T = np.maximum(0.30, 0.70 * dep)
    entry = (t < temp_th) | (dd <= -T)
    if fut_pos is not None:
        entry &= fut_pos < fut_max
    return pd.DataFrame({"temp": t, "dd": dd, "T": T, "entry": entry,
                         "reason": np.where(t < temp_th, "temp", "drawdown")})


STATE_RULES = {
    "WATCH": "temp<20 或 dd≤-0.7×T: 加入观察池(仓位0)",
    "CANDIDATE": "entry=True: 首仓(30/100)",
    "ADD1": "较持仓均价 -15%: 加35/100",
    "ADD2": "较持仓均价 -30%: 加35/100",
    "HOLD": "持有并监控顶部",
    "TOP_WARN": "temp>75 或 顶部预警分>阈值: 评估减仓",
    "EXIT": "temp>85(非金融)/75(金融) 或 趋势破坏: 清仓",
}
