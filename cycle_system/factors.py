# -*- coding: utf-8 -*-
"""
量化因子库：周期股专用因子计算。

所有函数假设输入为以 date 为索引、包含 close/open/high/low/volume/amount 的 DataFrame
（由 data 层规范化后传入）。

因子清单
--------
趋势/动量:  ma20/60/200, mom20/60/120, ma 排列得分
相对强度:   rs20/60/120（相对沪深300超额）
周期位置:   price_pos（5年区间位置）, dd_from_high, runup_from_low
估值:       pb_pct（PB 滚动分位, 周期股核心估值因子）
广度:       breadth（站上MA200占比）, new_high（创一年新高占比）
量能:       vol_surge（成交额相对一年均值倍数）
摆动结构:   zigzag 高低点（历史周期划分用）
"""
import numpy as np
import pandas as pd


# ---------------- 趋势与动量 ----------------

def add_ma(df, windows=(20, 60, 200)):
    for n in windows:
        df[f"ma{n}"] = df["close"].rolling(n, min_periods=n).mean()
    return df


def add_momentum(df, windows=(20, 60, 120)):
    for n in windows:
        df[f"mom{n}"] = df["close"].pct_change(n)
    return df


def ma_alignment_score(df):
    """均线排列得分 0-100：
    100 = ma20>ma60>ma200 完全多头; 60 = ma20>ma60; 40 = close>ma200; 0 = 空头。
    """
    c = df["close"]
    score = pd.Series(0.0, index=df.index)
    score = score.where(~(c > df["ma200"]), 40.0)
    score = score.where(~((df["ma20"] > df["ma60"]) & (c > df["ma200"])), 60.0)
    score = score.where(~((df["ma20"] > df["ma60"]) & (df["ma60"] > df["ma200"])), 100.0)
    return score


def add_relative_strength(df, bench_close, windows=(20, 60, 120)):
    """相对基准(默认沪深300)超额收益。"""
    b = bench_close.reindex(df.index).ffill()
    for n in windows:
        df[f"rs{n}"] = df["close"].pct_change(n) - b.pct_change(n)
    return df


# ---------------- 周期位置 ----------------

def add_price_position(df, window=1250):
    """price_pos: 收盘价在滚动 window（默认5年）高低区间的位置 0-1。"""
    roll_max = df["close"].rolling(window, min_periods=250).max()
    roll_min = df["close"].rolling(window, min_periods=250).min()
    rng = (roll_max - roll_min).replace(0, np.nan)
    df["price_pos"] = ((df["close"] - roll_min) / rng).clip(0, 1)
    df["dd_from_high"] = df["close"] / roll_max - 1
    df["runup_from_low"] = df["close"] / roll_min - 1
    return df


def add_volume_surge(df, window=250):
    """量能放大：当日成交额 / 一年均值。>1.5 视为显著放量。"""
    ma_amt = df["amount"].rolling(window, min_periods=60).mean()
    df["vol_surge"] = df["amount"] / ma_amt
    return df


# ---------------- 估值（周期股核心：PB 分位） ----------------

def rolling_pct_rank(s: pd.Series, window=1250, min_periods=250) -> pd.Series:
    """当日值在滚动窗口内的分位数(0-1)。"""
    def _rank(arr):
        return float((arr <= arr[-1]).mean())
    return s.rolling(window, min_periods=min_periods).apply(_rank, raw=True)


def add_pb_percentile(df, pb_col="pb", window=1250):
    if pb_col in df.columns and df[pb_col].notna().sum() > 250:
        df["pb_pct"] = rolling_pct_rank(df[pb_col], window=window)
    return df


# ---------------- 板块广度 ----------------

def compute_breadth(stock_closes: pd.DataFrame) -> pd.DataFrame:
    """输入个股收盘价宽表 (date × stock)，输出板块广度指标。

    breadth:  站上 MA200 的个股占比（>0.5 健康）
    new_high: 收盘价创 250 日新高的个股占比
    """
    ma200 = stock_closes.rolling(200, min_periods=100).mean()
    valid = stock_closes.notna() & ma200.notna()
    above = (stock_closes > ma200).where(valid)
    breadth = above.mean(axis=1)

    roll_max = stock_closes.rolling(250, min_periods=100).max()
    new_high = (stock_closes >= roll_max * 0.999).where(valid)
    nh = new_high.mean(axis=1)

    out = pd.DataFrame({"breadth": breadth, "new_high": nh})
    # 广度需要足够有效样本（至少5只同时有价格与均线）
    valid_n = valid.sum(axis=1)
    out.loc[valid_n < 5, ["breadth", "new_high"]] = np.nan
    return out


# ---------------- 摆动结构（历史周期划分） ----------------

def zigzag(close: pd.Series, pct=0.25):
    """ZigZag 摆动点识别（基于对数收益阈值）。

    参数
    ----
    close : 收盘价序列（date 索引）
    pct   : 反转幅度阈值（如 0.25 = 反向波动 25% 确认一个摆动点）

    返回
    ----
    list[(date, 'H'|'L', price)]，按时间排序。
    注意：摆动点在反向波动达阈值后才确认 —— 仅用于历史周期描述，不可作交易信号。
    """
    n = len(close)
    if n < 60:
        return []
    vals = np.asarray(close.values, dtype=float)
    idx = close.index
    pivots = []  # (pos, 'H'|'L', value)

    # 初始方向: 前60日内低点在高点之前→上行(跟踪最大值), 否则下行(跟踪最小值)
    window = min(60, n)
    hi_pos = int(np.argmax(vals[:window]))
    lo_pos = int(np.argmin(vals[:window]))
    if hi_pos > lo_pos:
        direction = 1
        ext_pos, ext_val = hi_pos, vals[hi_pos]
    else:
        direction = -1
        ext_pos, ext_val = lo_pos, vals[lo_pos]

    for i in range(1, n):
        v = vals[i]
        if direction == 1:
            if v >= ext_val:
                ext_pos, ext_val = i, v
            elif ext_val > 0 and v < ext_val * np.exp(-pct):
                pivots.append((ext_pos, "H", ext_val))
                direction = -1
                ext_pos, ext_val = i, v
        else:
            if v <= ext_val:
                ext_pos, ext_val = i, v
            elif ext_val > 0 and v > ext_val * np.exp(pct):
                pivots.append((ext_pos, "L", ext_val))
                direction = 1
                ext_pos, ext_val = i, v

    return [(idx[p], t, float(v)) for (p, t, v) in pivots]


def cycle_segments(close: pd.Series, pct=0.25, min_days=60):
    """将 zigzag 摆动点组织为周期段。

    返回 (ups, downs):
      ups:   [{"bottom", "top", "ret", "days"}, ...] 每轮上行（底→顶）
      downs: [{"top", "bottom", "ret", "days"}, ...] 每轮下行（顶→底）
    仅保留跨度 >= min_days 的段（过滤噪音）。
    """
    piv = zigzag(close, pct=pct)
    ups, downs = [], []
    for i in range(len(piv) - 1):
        (d0, t0, p0), (d1, t1, p1) = piv[i], piv[i + 1]
        days = (d1 - d0).days
        if days < min_days:
            continue
        if t0 == "L" and t1 == "H":
            ups.append({"bottom": d0, "top": d1, "ret": p1 / p0 - 1, "days": days})
        elif t0 == "H" and t1 == "L":
            downs.append({"top": d0, "bottom": d1, "ret": p1 / p0 - 1, "days": days})
    return ups, downs
