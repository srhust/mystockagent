# -*- coding: utf-8 -*-
"""cycle_metrics.py (CycleTrend-v1 Phase1): 单周期评价体系。
输入: fills(专户成交), MU价格, 周期窗口[trough_idx, peak_idx], budget。
输出: Cycle Return/年化/MFE/MAE/捕获率/浮盈回吐/资金占用/持有天数。未平仓按当前市值计价。"""
import numpy as np
import pandas as pd


def cycle_report(fills, close, dates, i_trough, i_peak, budget, end_idx=None):
    """fills: [(date_iso, side, shares, px)]; close/dates: 全序列; 周期窗口按位置索引。
    逻辑: 窗口起点前60日至peak之间的BUY/ADD归属本周期; 其后首个SELL为出场; 无SELL则未平仓按end_idx市值。"""
    end_idx = len(close) - 1 if end_idx is None else end_idx
    t0 = dates[i_trough]
    t1 = dates[i_peak]
    buys = [f for f in fills if f[1] in ("BUY", "ADD") and t0 is not None
            and pd.Timestamp(f[0]) >= pd.Timestamp(t0) - pd.Timedelta(days=90)
            and pd.Timestamp(f[0]) <= pd.Timestamp(t1) + pd.Timedelta(days=200)]
    if not buys:
        return None
    first_buy_d = pd.Timestamp(buys[0][0])
    i_entry = dates.get_indexer([first_buy_d])[0]
    i_exit = None
    for f in fills:
        if f[1] == "SELL" and pd.Timestamp(f[0]) > pd.Timestamp(buys[-1][0]):
            i_exit = dates.get_indexer([pd.Timestamp(f[0])])[0]
            break
    i_end = i_exit if i_exit is not None else min(end_idx, i_peak + 250)
    spent = sum(f[2] * f[3] for f in buys)
    shares = sum(f[2] for f in buys)
    avg_cost = spent / shares if shares else np.nan
    px_path = close.iloc[i_entry:i_end + 1].values
    px_path = px_path[np.isfinite(px_path)]
    cur_px = float(close.iloc[i_end])
    exit_value = shares * cur_px
    cost_basis = spent * 1.001
    ret = exit_value / cost_basis - 1
    hold_days = int(i_end - i_entry)
    mfe = float(px_path.max() / avg_cost - 1) if len(px_path) else np.nan
    mae = float(px_path.min() / avg_cost - 1) if len(px_path) else np.nan
    peak_px = float(close.iloc[i_peak])
    trough_px = float(close.iloc[i_trough])
    ref_up = peak_px / trough_px - 1
    capture = (cur_px / avg_cost - 1) / ref_up if ref_up > 0 else np.nan
    peak_float = float(px_path.max()) if len(px_path) else cur_px
    giveback = (peak_float - cur_px) / peak_float if peak_float > 0 else np.nan
    util = spent / budget
    ann = (1 + ret) ** (365.25 / max(1, (pd.Timestamp(dates[i_end]) - first_buy_d).days)) - 1 if hold_days > 5 else np.nan
    return dict(entry=str(first_buy_d.date()), exit=str(pd.Timestamp(dates[i_end]).date()),
                ret=round(float(ret), 3), ann=round(float(ann), 2), hold_d=hold_days,
                mfe=round(mfe, 2), mae=round(mae, 2), capture=round(float(capture), 2),
                giveback=round(float(giveback), 2), util=round(float(util), 2),
                closed=int(i_exit is not None))
