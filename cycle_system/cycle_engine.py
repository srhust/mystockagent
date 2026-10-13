# -*- coding: utf-8 -*-
"""
周期刻画引擎：周期温度计 + 阶段状态机。

温度计（0-100，高=周期火热，低=周期冰点）由五个维度加权：
    价格位置(25%) + 动量(25%) + 估值PB分位(25%) + 板块广度(15%) + 量能(10%)
缺失维度自动权重重归一化。

阶段状态机（非前瞻，只用截至当日信息）：
    decline(下行) -> bottom(底部) -> recovery(启动) -> expansion(扩张)
                                        |                |
                                        v                v
                                    decline  <------  topping(见顶)
"""
import numpy as np
import pandas as pd

import config
from . import factors


# ---------------- 温度计 ----------------

def compute_temperature(df, params=None):
    params = params or config.TEMPERATURE

    # 1. 价格位置分: 5年区间位置 0-1 -> 0-100
    price_score = df["price_pos"] * 100.0

    # 2. 动量分: 均线排列(60%) + 60日动量映射(40%)
    ma_score = factors.ma_alignment_score(df)
    mom_score = (df["mom60"].clip(-0.25, 0.25) / 0.25 * 50.0 + 50.0).clip(0, 100)
    momentum_score = 0.6 * ma_score + 0.4 * mom_score

    # 3. 估值分: PB 滚动分位（周期高位→PB分位高→热）
    val_score = (df["pb_pct"] * 100.0) if "pb_pct" in df.columns else pd.Series(np.nan, index=df.index)

    # 4. 广度分: 站上MA200占比
    breadth_score = (df["breadth"] * 100.0) if "breadth" in df.columns else pd.Series(np.nan, index=df.index)

    # 5. 量能分: 成交额放大倍数 0.5~2.5 映射 0~100
    vol_score = ((df["vol_surge"] - 0.5) / 2.0).clip(0, 1) * 100.0

    scores = pd.DataFrame({
        "price": price_score,
        "mom": momentum_score,
        "val": val_score,
        "breadth": breadth_score,
        "vol": vol_score,
    })
    weights = pd.Series({
        "price": params["w_price"],
        "mom": params["w_momentum"],
        "val": params["w_valuation"],
        "breadth": params["w_breadth"],
        "vol": params["w_volume"],
    })
    num = scores.mul(weights).sum(axis=1, skipna=True)
    den = scores.notna().astype(float).mul(weights, axis=1).sum(axis=1)
    temp = (num / den.replace(0, np.nan)).clip(0, 100)
    return temp


# ---------------- 阶段状态机 ----------------

def detect_stage(df, thr=None):
    """逐日输出周期阶段标签（非前瞻）。"""
    thr = thr or config.STAGE_THRESHOLDS

    temp = df["temp"]
    c, ma20, ma60, ma200 = df["close"], df["ma20"], df["ma60"], df["ma200"]
    dd = df["dd_from_high"] if "dd_from_high" in df.columns else pd.Series(0.0, index=df.index)
    pbp = df["pb_pct"] if "pb_pct" in df.columns else pd.Series(0.0, index=df.index)
    mom20 = df["mom20"]

    above200 = c > ma200
    ma_up = ma20 > ma60
    ma_stack = (ma20 > ma60) & (ma60 > ma200)
    death_cross = ~ma_up
    has_data = temp.notna() & ma200.notna() & mom20.notna()

    recovery_sig = (temp > thr["recovery"]) & above200 & ma_up & (mom20 > 0)
    expansion_sig = (temp > thr["expansion"]) & above200 & ma_stack
    topping_sig = (temp > thr["topping"]) & (
        death_cross | (dd < -thr["drawdown_top"]) | (pbp > thr["pb_hot"]))
    decline_sig = death_cross & ((temp < thr["decline"]) | (dd < -2 * thr["drawdown_top"]))
    # 底部确认必须"止跌企稳"：温度低 + 短期动量不再下行
    # (深寒<20 用较松企稳阈值, 寒冷<35 用较严阈值)
    stab = thr["stabilize_mom20"]
    stab_loose = stab * 2  # 例如 -0.06
    bottom_sig = ((temp < thr["deep_cold"]) & (mom20 > stab_loose)) | (
        (temp < thr["bottom"]) & (mom20 > stab))

    # 布尔列填充 False 处理 NaN
    def b(s):
        return s.fillna(False)

    recovery_sig, expansion_sig = b(recovery_sig), b(expansion_sig)
    topping_sig, decline_sig, bottom_sig = b(topping_sig), b(decline_sig), b(bottom_sig)

    # 转 numpy 加速逐日状态机
    temp_v = temp.fillna(-1).values
    rec_v = recovery_sig.values
    exp_v = expansion_sig.values
    top_v = topping_sig.values
    dec_v = decline_sig.values
    bot_v = bottom_sig.values
    dc_v = death_cross.values
    has_v = has_data.values

    n = len(df)
    out = np.empty(n, dtype=object)
    out[:] = "warmup"
    state = "decline"
    started = False
    for i in range(n):
        if not started:
            if has_v[i]:
                started = True
                if bot_v[i]:
                    state = "bottom"
                elif rec_v[i]:
                    state = "recovery"
                elif exp_v[i]:
                    state = "expansion"
                else:
                    state = "decline"
            out[i] = "warmup" if not started else state
            continue
        if state == "decline":
            if bot_v[i]:
                state = "bottom"
            elif rec_v[i]:
                state = "recovery"
        elif state == "bottom":
            if rec_v[i]:
                state = "recovery"
        elif state == "recovery":
            if exp_v[i]:
                state = "expansion"
            elif top_v[i]:
                state = "topping"
            elif dec_v[i]:
                state = "decline"
        elif state == "expansion":
            if top_v[i]:
                state = "topping"
            elif dec_v[i]:
                state = "decline"
        elif state == "topping":
            if dec_v[i]:
                state = "decline"
            elif (temp_v[i] < thr["expansion"]) and exp_v[i]:
                # 冷却到扩张线以下且趋势重新确立（迟滞，防止高位反复切换）
                state = "expansion"
        out[i] = state
    return pd.Series(out, index=df.index, name="stage")


STAGE_CN = {
    "decline": "下行",
    "bottom": "底部",
    "recovery": "启动",
    "expansion": "扩张",
    "topping": "见顶",
    "warmup": "数据不足",
}


# ---------------- 特征工程管道 ----------------

def enrich_sector(df, bench_close, stock_closes=None, pb_series=None, params=None):
    """对单个板块做全部特征工程。

    Parameters
    ----------
    df : 规范化行情 DataFrame（含 date, close, amount 列）
    bench_close : 基准指数收盘价 Series（date 索引）
    stock_closes : 个股收盘价宽表（date × stock），可为 None
    pb_series : 板块 PB 中位数序列（date 索引），可为 None
    """
    params = params or config.TEMPERATURE
    df = df.copy()
    if "date" in df.columns:
        df = df.set_index("date")
    df = df.sort_index()

    df = factors.add_ma(df)
    df = factors.add_momentum(df)
    df = factors.add_relative_strength(df, bench_close)
    df = factors.add_price_position(df, window=params["price_window"])
    df = factors.add_volume_surge(df, window=params["vol_window"])

    if stock_closes is not None and stock_closes.shape[1] >= 5:
        br = factors.compute_breadth(stock_closes)
        # 对齐到板块交易日（个股停牌等造成缺口时前向填充）
        br = br.reindex(df.index).ffill(limit=10)
        df = df.join(br, how="left")
    if pb_series is not None and len(pb_series.dropna()) > 100:
        # 百度估值为稀疏日期(约每5日), 对齐到板块交易日并前向填充
        pb_aligned = pb_series.reindex(df.index)
        # 先在 pb 自身日期上填充到日历日再对齐: 用 union index + ffill
        union_idx = df.index.union(pb_series.index)
        pb_full = pb_series.reindex(union_idx).ffill(limit=15)
        pb_aligned = pb_full.reindex(df.index)
        df["pb"] = pb_aligned
        df = factors.add_pb_percentile(df, window=params["pb_window"])

    df["temp"] = compute_temperature(df, params)
    df["stage"] = detect_stage(df)
    return df


def stage_transitions(stage_series):
    """提取阶段切换事件。返回 DataFrame(date, from, to)。"""
    s = stage_series.dropna()
    ch = s[s != s.shift(1)]
    events = []
    vals = ch.values
    idx = ch.index
    for i in range(1, len(vals)):
        events.append({"date": idx[i], "from": vals[i - 1], "to": vals[i]})
    return pd.DataFrame(events)
