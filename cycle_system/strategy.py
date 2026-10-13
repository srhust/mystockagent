# -*- coding: utf-8 -*-
"""
买卖策略：基于周期阶段的仓位信号 + 组合构建。

双轨策略
--------
右侧（胜率轨）: 周期启动确认（阶段进入 recovery）买入 → 见顶/下行卖出
左侧（赔率轨）: 深度冰点（温度<20 且 PB分位<10%）提前分批布局

卖出
----
- 阶段进入 topping / decline
- 移动止损：持有期内自最高收盘回撤超阈值
"""
import numpy as np
import pandas as pd

import config


def _temp_hysteresis(temp: pd.Series, enter_below: float, exit_above: float) -> pd.Series:
    """温度迟滞状态机: 温度低于 enter_below 置1(买入), 高于 exit_above 置0(卖出), 中间保持。

    这是"深寒左侧买入、持有至周期过热"的核心状态机。
    """
    v = temp.values.astype(float)
    n = len(v)
    out = np.zeros(n, dtype=int)
    state = 0
    for i in range(n):
        t = v[i]
        if np.isnan(t):
            out[i] = state
            continue
        if state == 0:
            if t < enter_below:
                state = 1
        else:
            if t > exit_above:
                state = 0
        out[i] = state
    return pd.Series(out, index=temp.index, name="position")


def _overheat_block(temp: pd.Series, stage: pd.Series, block_above=78, unblock_below=15):
    """过热封锁: 温度超过 block_above 后禁止做多, 直到温度回落到 unblock_below 以下或阶段重新启动。"""
    v = temp.values.astype(float)
    s = stage.values if stage is not None else np.array([""] * len(v), dtype=object)
    n = len(v)
    out = np.ones(n, dtype=int)
    blocked = False
    for i in range(n):
        t = v[i]
        if not np.isnan(t):
            if t > block_above:
                blocked = True
            if blocked and (t < unblock_below or s[i] == "recovery"):
                blocked = False
        out[i] = 0 if blocked else 1
    return pd.Series(out, index=temp.index)


def _cold_v2_position(df: pd.DataFrame, enter_below: float, exit_above: float) -> pd.Series:
    """V2 深寒 + 期货背离提前出场。

    入场: temp < enter_below（与 cold 相同, 左侧深寒不动——已验证任何确认式入场都更差）。
    出场: temp > exit_above（原有） 或 期货背离提前撤退:
          temp > 65 且 对应商品期货 20 日动量 < 0 且 期货 < 其 60 日均线。
    机制: 周期股见顶顺序 = 商品滞涨回落 → 股票惯性冲高 → 崩; 商品期货是实物定价锚,
    在第一环撤退（2026-10 两窗口验证: OOS +10.5%/Sharpe 0.62/MDD -40.5% 全胜基线）。
    出场后需重新满足深寒入场条件才会再进。期货列缺失时退化为普通 cold。
    """
    temp = df["temp"]
    fm = df["fut_mom20"] if "fut_mom20" in df.columns else None
    fb = df["fut_below_ma60"] if "fut_below_ma60" in df.columns else None
    if fm is None or fb is None:
        return _temp_hysteresis(temp, enter_below, exit_above)
    t = temp.values.astype(float)
    m = fm.values.astype(float)
    b = fb.values.astype(bool)
    n = len(t)
    out = np.zeros(n, dtype=int)
    state = 0
    for i in range(n):
        ti, mi = t[i], m[i]
        if np.isnan(ti):
            out[i] = state
            continue
        if state == 1:
            if ti > exit_above:
                state = 0
            elif ti > 65 and np.isfinite(mi) and mi < 0 and b[i]:
                state = 0
        else:
            if ti < enter_below:
                state = 1
        out[i] = state
    return pd.Series(out, index=temp.index, name="position")


def position_target(df: pd.DataFrame, mode="right",
                    enter_below: float = None, exit_above: float = None) -> pd.Series:
    """逐日目标仓位（1=持有, 0=空仓）。

    mode='right'  : 右侧确认 —— stage in (recovery, expansion) 持有
    mode='dual'   : 右侧 + 左侧深度冰点布局(temp<20 & pb分位<10%)
    mode='cold'   : 深寒左侧 —— temp<12 买入, temp>75 卖出（数据实证最优温区）
    mode='hybrid' : 深寒(15)/右侧启动 买入 + 过热(78)封锁卖出

    enter_below / exit_above: 覆盖 config 默认阈值（用于参数扫描/样本外验证,
    None = 用 config.STRATEGY 默认值）。
    """
    stage = df["stage"] if "stage" in df.columns else pd.Series("warmup", index=df.index)
    stage = stage.where(stage != "warmup", "n/a")

    if mode in ("cold", "hybrid"):
        if mode == "cold":
            pos = _temp_hysteresis(df["temp"],
                                   enter_below=enter_below if enter_below is not None
                                   else config.STRATEGY["buy"]["cold"]["enter_below"],
                                   exit_above=exit_above if exit_above is not None
                                   else config.STRATEGY["sell"]["cold_exit_above"])
        else:
            _eb = enter_below if enter_below is not None \
                else config.STRATEGY["buy"]["hybrid_cold"]["enter_below"]
            cold = _temp_hysteresis(df["temp"],
                                    enter_below=_eb,
                                    exit_above=1e9)  # cold 仓不因温度自行退出
            right = stage.isin(["recovery", "expansion"]).astype(int)
            block = _overheat_block(df["temp"], stage,
                                    block_above=config.STRATEGY["sell"]["hot_block_above"],
                                    unblock_below=config.STRATEGY["buy"]["hybrid_cold"]["enter_below"])
            pos = ((cold | right) & block).astype(int)
        return pos.astype(int)

    if mode == "cold_v2":
        return _cold_v2_position(
            df,
            enter_below if enter_below is not None
            else config.STRATEGY["buy"]["cold"]["enter_below"],
            exit_above if exit_above is not None
            else config.STRATEGY["sell"]["cold_exit_above"]).astype(int)

    long_right = stage.isin(["recovery", "expansion"])

    if mode == "dual":
        cold = (df["temp"] < config.STRATEGY["buy"]["left_side"]["temp_below"])
        pb_low = pd.Series(False, index=df.index)
        if "pb_pct" in df.columns:
            pb_low = df["pb_pct"] < config.STRATEGY["buy"]["left_side"]["pb_pct_below"]
        left = cold & pb_low
        pos = (long_right | left).astype(int)
    else:
        pos = long_right.astype(int)
    return pos.astype(int)


def apply_trailing_stop(df: pd.DataFrame, pos: pd.Series, dd_exit=None) -> pd.Series:
    """移动止损覆盖：持有期间自最高收盘回撤超阈值则清仓，直至阶段重新进入 recovery。"""
    dd_exit = dd_exit if dd_exit is not None else config.STRATEGY["sell"]["drawdown_exit"]
    close_v = df["close"].values
    stage_v = df["stage"].values if "stage" in df.columns else np.array([""] * len(df), dtype=object)
    pos_v = pos.values
    n = len(df)
    out = np.zeros(n, dtype=int)
    stopped = False
    peak = np.nan
    for i in range(n):
        if pos_v[i] == 1:
            c = close_v[i]
            peak = c if np.isnan(peak) else max(peak, c)
            if stopped:
                if stage_v[i] == "recovery":
                    stopped = False
                    peak = c
                    out[i] = 1
                else:
                    out[i] = 0
            else:
                if not np.isnan(peak) and c / peak - 1 < -dd_exit:
                    stopped = True
                    out[i] = 0
                    peak = np.nan
                else:
                    out[i] = 1
        else:
            out[i] = 0
            stopped = False
            peak = np.nan
    return pd.Series(out, index=df.index, name="position")


def signal_strength(df: pd.DataFrame) -> pd.Series:
    """板块信号强度（用于多板块竞争仓位时的排序）：动量 + 相对强度 + 温度上升速度。"""
    s = df.get("mom60", pd.Series(0.0, index=df.index)) + \
        df.get("rs60", pd.Series(0.0, index=df.index))
    temp_slope = df["temp"].diff(20) / 100.0 if "temp" in df.columns else pd.Series(0.0, index=df.index)
    return (s + temp_slope).fillna(0)


def current_snapshot(df: pd.DataFrame, name: str) -> dict:
    """当前时点快照（用于报告/决策）。"""
    last = df.iloc[-1]
    prev = df.iloc[-min(len(df), 60)]
    snap = {
        "sector": name,
        "date": df.index[-1],
        "close": float(last["close"]),
        "temp": float(last["temp"]) if np.isfinite(last.get("temp", np.nan)) else np.nan,
        "stage": last.get("stage", "n/a"),
        "stage_cn": None,
        "mom20": float(last.get("mom20", np.nan)),
        "mom60": float(last.get("mom60", np.nan)),
        "rs60": float(last.get("rs60", np.nan)),
        "price_pos": float(last.get("price_pos", np.nan)),
        "dd_from_high": float(last.get("dd_from_high", np.nan)),
        "runup_from_low": float(last.get("runup_from_low", np.nan)),
        "pb": float(last.get("pb", np.nan)),
        "pb_pct": float(last.get("pb_pct", np.nan)),
        "breadth": float(last.get("breadth", np.nan)),
        "vol_surge": float(last.get("vol_surge", np.nan)),
        "temp_chg60": float(last.get("temp", np.nan) - prev.get("temp", np.nan)) if np.isfinite(last.get("temp", np.nan)) else np.nan,
    }
    from .cycle_engine import STAGE_CN
    snap["stage_cn"] = STAGE_CN.get(snap["stage"], snap["stage"])
    return snap
