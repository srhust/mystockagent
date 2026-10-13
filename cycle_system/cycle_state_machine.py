# -*- coding: utf-8 -*-
"""cycle_state_machine.py (P0): 三级买点状态机 + 失效条件 + 顶部退出衔接。
状态: FLAT -> BOTTOM_WATCH(观察, 不产生买入) -> EARLY_REVERSAL(试探研究) -> CYCLE_START(可执行候选)
      -> HOLD -> EXIT(顶部锚或dd修复出场)。快速通道: 深跌+放量+广度急升直接 WATCH->CYCLE_START。
失效条件: 入场后收盘跌破MA20×0.97 或 创出信号后新低 -> 回退WATCH(候选作废)。"""
import numpy as np
import pandas as pd

EXIT_TOP, EXIT_DD = 85.0, -0.25


def run_machine(close, det, temp, dd, breadth=None, volume=None):
    """输入: close, reversal_detector输出det, 温度序列, 回撤序列。
    返回状态帧: state∈{FLAT,WATCH,EARLY,START,HOLD,EXIT}, fail_reason。"""
    n = len(close)
    ma20 = close.rolling(20).mean()
    state = pd.Series("FLAT", index=close.index)
    reason = pd.Series("", index=close.index)
    cur, entry_px, entry_i = "FLAT", np.nan, -1
    vol_surge = pd.Series(False, index=close.index)
    if volume is not None:
        vol_surge = volume.rolling(20).mean() > volume.rolling(60).mean() * 1.8
    fast_lane = (det["watch"] > 0) & vol_surge & (det["bd"] > 0.05)
    for i in range(n):
        s_w, s_e, s_s = det["watch"].iloc[i], det["early"].iloc[i], det["start"].iloc[i]
        px = close.iloc[i]
        if cur in ("START", "HOLD"):
            # 失效条件
            if px < entry_px * 0.90 or px < close.iloc[entry_i:max(i, entry_i + 1)].min() * 0.97:
                cur, reason.iloc[i] = "WATCH", "失效:跌破入场结构"
                entry_px = np.nan
            elif (temp.iloc[i] > EXIT_TOP) or (dd.iloc[i] >= EXIT_DD):
                cur, reason.iloc[i] = "EXIT", f"出场:temp={temp.iloc[i]:.0f} dd={dd.iloc[i]*100:.0f}%"
            else:
                cur = "HOLD"
                reason.iloc[i] = "持有"
        elif cur == "EXIT":
            cur = "WATCH" if s_w else "FLAT"
            reason.iloc[i] = "出场后观察"
        if cur in ("FLAT", "WATCH", "EARLY"):
            if fast_lane.iloc[i]:
                cur, reason.iloc[i] = "START", "快速通道:深跌+放量+广度急升"
                entry_px, entry_i = px, i
            elif s_s and (s_w or cur != "FLAT"):
                cur, reason.iloc[i] = "START", "三重确认:价格突破+RS+广度"
                entry_px, entry_i = px, i
            elif s_e:
                cur, reason.iloc[i] = "EARLY", "两项改善(动量/RS/广度)"
            elif s_w:
                cur, reason.iloc[i] = "WATCH", "潜在低位"
            elif cur in ("WATCH", "EARLY") and not s_w:
                cur, reason.iloc[i] = "FLAT", "观察失效"
        state.iloc[i] = cur
    return pd.DataFrame({"state": state, "reason": reason})
