# -*- coding: utf-8 -*-
"""指令④: 底部与顶部模型拆分。两套锚各自独立评估, 不再共用一套"温度"叙事。
底部锚(相对回撤+产业出清): 研究目标是 Recall/Precision/MAE——尽可能贴底。
顶部锚(盈利变化+商品背离+趋势衰竭): 研究目标是提前量与误报率——尽可能早走。
冻结: 不再调 12/75/85 阈值; 顶部锚的分量在模拟盘中只输出预警分, 不直接触发交易。"""
import numpy as np
import pandas as pd


# ---------------- 底部锚 ----------------
def bottom_score(close, fut_pos=None, *, floor=0.30, k=0.70, win=1250, temp_th=12.0):
    """底部锚: temp_v4_price<12 ∨ dd<=-max(floor, k×最深回撤)。产业出清(fut_pos低)作确认分。"""
    lo, hi = close.rolling(win, min_periods=500).min(), close.rolling(win, min_periods=500).max()
    pp = (close - lo) / (hi - lo)
    r120 = close.pct_change(120).rolling(win, min_periods=500).rank(pct=True)
    r60 = close.pct_change(60).rolling(win, min_periods=500).rank(pct=True)
    temp = 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)
    dd = close / close.cummax() - 1.0
    dep = (-dd).rolling(win, min_periods=250).max()
    thr = np.maximum(floor, k * dep)
    entry = ((temp < temp_th) | (dd <= -thr)) & (temp > 0)
    out = pd.DataFrame({"temp": temp, "dd": dd, "thr": thr, "entry": entry.astype(int)})
    if fut_pos is not None:
        out["industry_confirm"] = (fut_pos < 0.60).astype(int)  # 产业出清确认(高分=更可信)
    return out


# ---------------- 顶部锚 ----------------
def top_score(close, volume=None, earnings_chg=None, fut_pos=None, *, win=1250):
    """顶部预警分 S_top∈[0,3]: 趋势衰竭 + 量能背离 + 盈利变化 + 商品背离(各项0/1)。
    仅作预警输出(模拟盘T项), 不直接触发交易; 评估指标=预警提前量/误报率。"""
    ret120 = close.pct_change(120)
    r120 = ret120.rolling(win, min_periods=500).rank(pct=True)
    t_hi = pd.DataFrame({"x": close}).rolling(win, min_periods=500).rank(pct=True)["x"]  # 价格分位
    exhaustion = ((t_hi > 0.90) & (r120 < 0.5)).astype(int)  # 高位但动量衰竭
    vol_div = pd.Series(0, index=close.index)
    if volume is not None:
        vma = volume.rolling(20).mean()
        new_hi = close >= close.rolling(60).max() * 0.995
        vol_div = (new_hi & (vma < vma.rolling(60).mean() * 0.9)).astype(int)  # 新高缩量
    earn = pd.Series(0, index=close.index) if earnings_chg is None else (earnings_chg < 0).astype(int)
    fut_div = pd.Series(0, index=close.index)
    if fut_pos is not None:
        mom20 = fut_pos.pct_change(20)
        fut_div = ((fut_pos > 0.8) & (mom20 < 0)).astype(int)  # 商品高位回落=背离
    s_top = exhaustion + vol_div + earn + fut_div
    return pd.DataFrame({"exhaustion": exhaustion, "vol_div": vol_div,
                         "earnings_neg": earn, "commodity_div": fut_div, "S_top": s_top})
