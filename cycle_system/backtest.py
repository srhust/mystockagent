# -*- coding: utf-8 -*-
"""
回测引擎：多板块轮动组合回测。

规则
----
- 信号在 T 日收盘计算；T+1 日收盘成交（保守，无未来函数）
- 组合最多持有 max_positions 个板块，等权
- 同一周期类别最多 per_category_max 个板块
- 卖出：目标仓位归零（阶段转差）或移动止损触发
- 买入候选排序：信号强度（动量+相对强度+温度斜率）
- 成本：单边 cost_rate（默认 0.15%：佣金+滑点+冲击）
"""
import numpy as np
import pandas as pd

import config
from . import strategy as strat


def run_backtest(dfs: dict, meta: dict, mode="right", cost_rate=0.0015,
                 max_positions=None, per_category_max=None, trailing_stop=True,
                 start=None, end=None, strat_params=None):
    """运行组合回测。

    Parameters
    ----------
    dfs : {板块名: enriched DataFrame（date 索引, 含 close/stage/temp...）}
    meta : {板块名: {"cat": 周期类别, ...}}（universe 信息）
    mode : 'right' 右侧 / 'dual' 双轨 / 'cold' 深寒左侧 / 'hybrid' 混合
    strat_params : 透传给 strategy.position_target 的参数覆盖
        （如 {"enter_below": 10, "exit_above": 70}, 用于参数扫描/样本外验证）

    Returns
    -------
    dict(equity: DataFrame, trades: DataFrame, metrics: dict, daily_weights: DataFrame)
    """
    if max_positions is None:
        max_positions = (config.STRATEGY.get("cold_max_positions")
                         if mode in ("cold", "hybrid") else config.STRATEGY["max_positions"])
    per_category_max = per_category_max or config.STRATEGY["per_category_max"]
    dd_exit = config.STRATEGY["trailing_stop"].get(mode, 0.15)
    strat_params = strat_params or {}

    # 1. 每个板块的目标仓位（含移动止损覆盖）
    targets = {}
    for name, df in dfs.items():
        pos = strat.position_target(df, mode=mode, **strat_params)
        if trailing_stop:
            pos = strat.apply_trailing_stop(df, pos, dd_exit=dd_exit)
        targets[name] = pos

    # 2. 统一日历（用并集）
    all_dates = sorted(set().union(*[set(df.index) for df in dfs.values()]))
    cal = pd.DatetimeIndex(all_dates)
    if start:
        cal = cal[cal >= pd.Timestamp(start)]
    if end:
        cal = cal[cal <= pd.Timestamp(end)]

    closes = pd.DataFrame({name: df["close"] for name, df in dfs.items()}).reindex(cal)
    rets = closes.pct_change().fillna(0.0)
    # P0修正: T日收盘后生成的信号, T+1日才可成交(此处以T+1收盘价撮合)。
    # 信号整体后移一天: tgt.loc[d] 实为 T=d-1 收盘后信号, 成交价 closes.loc[d]。
    tgt = pd.DataFrame(targets).reindex(cal).fillna(0).astype(int).shift(1)
    strength = pd.DataFrame({name: strat.signal_strength(df) for name, df in dfs.items()}).reindex(cal).fillna(0).shift(1)

    n_days = len(cal)
    sectors = list(dfs.keys())

    # 3. 逐日模拟
    weights = pd.Series(0.0, index=sectors)
    equity = 1.0
    eq_curve = np.zeros(n_days)
    w_hist = []
    trades = []
    entry_prices = {}   # sector -> (price, date)
    day_cost = 0.0

    for i, d in enumerate(cal):
        # 当日收益：昨日权重 × 今日板块收益
        if i > 0:
            day_ret = float((weights * rets.loc[d]).sum())
            equity *= (1 + day_ret)

        # ---- 收盘调仓 ----
        new_weights = weights.copy()
        # 卖出
        for s in sectors:
            if weights[s] > 0 and tgt.loc[d, s] == 0:
                px = closes.loc[d, s]
                if np.isfinite(px) and s in entry_prices and entry_prices[s]:
                    ep, ed = entry_prices[s]
                    trades.append({
                        "sector": s, "entry_date": ed, "exit_date": d,
                        "entry_price": ep, "exit_price": px,
                        "ret": px / ep - 1,
                        "days": (d - ed).days,
                        "exit_reason": "signal/trailing",
                    })
                new_weights[s] = 0.0
                entry_prices.pop(s, None)
        # 买入（有仓位空位时）
        held = [s for s in sectors if new_weights[s] > 0]
        slots = max_positions - len(held)
        if slots > 0:
            cats_held = {}
            for s in held:
                c = meta.get(s, {}).get("cat", "其他")
                cats_held[c] = cats_held.get(c, 0) + 1
            cands = [s for s in sectors
                     if new_weights[s] == 0 and tgt.loc[d, s] == 1 and np.isfinite(closes.loc[d, s])]
            cands.sort(key=lambda s: strength.loc[d, s], reverse=True)
            for s in cands:
                if slots <= 0:
                    break
                c = meta.get(s, {}).get("cat", "其他")
                if cats_held.get(c, 0) >= per_category_max:
                    continue
                new_weights[s] = 1.0 / max_positions
                entry_prices[s] = (float(closes.loc[d, s]), d)
                cats_held[c] = cats_held.get(c, 0) + 1
                slots -= 1
        # 归一化（卖出后闲置资金计入现金）
        turnover = float((new_weights - weights).abs().sum())
        day_cost = turnover * cost_rate
        equity *= (1 - day_cost)
        eq_curve[i] = equity
        weights = new_weights
        w_hist.append(weights.copy())

    equity_df = pd.DataFrame({"equity": eq_curve}, index=cal)
    w_df = pd.DataFrame(w_hist, index=cal)
    trades_df = pd.DataFrame(trades)
    metrics = compute_metrics(equity_df, trades_df, rets)
    return {"equity": equity_df, "weights": w_df, "trades": trades_df, "metrics": metrics}


def overlay_equity(w_df: pd.DataFrame, rets: pd.DataFrame, cost_rate=0.0015,
                   dd_soft=-0.15, dd_hard=-0.30, recover_above=-0.08, soft_scale=0.5,
                   vol_target=None, vol_window=60):
    """组合级风控覆盖层：在已有权重序列上模拟回撤控制/波动率目标（非前瞻）。

    规则（T 日收盘可知信息 → 调 T+1 日权重, 与回测引擎的时序约定一致）:
    - 回撤控制（迟滞状态机, 状态: normal/reduced/flat）:
        normal: 组合自高点回撤 <= dd_soft (如-15%)
        reduced: 回撤进入 (dd_hard, dd_soft] → 权重 × soft_scale (如0.5)
        flat: 回撤 <= dd_hard (如-30%) → 权重 × 0
        恢复: reduced/flat 状态下回撤修复到 recover_above (如-8%) 以上 → normal
    - 波动率目标: 若叠加后组合近 vol_window 日年化波动 > vol_target,
        权重再 × min(1, vol_target/realized_vol)（下限 0.2, 防止除零归零）
    - 缩放产生的额外换手按 cost_rate 计入成本（保守）

    Parameters
    ----------
    w_df : run_backtest 返回的 weights（index=日历, 列=板块, 值=目标权重）
    rets : 板块日收益 DataFrame（与 run_backtest 内部一致: closes.pct_change().fillna(0)）

    Returns
    -------
    dict(equity: DataFrame, weights: DataFrame(缩放后), metrics: dict)
    """
    cal = w_df.index
    sectors = list(w_df.columns)
    n = len(cal)
    eq = 1.0
    peak = 1.0
    state = "normal"          # normal / reduced / flat
    eq_curve = np.zeros(n)
    ret_hist = []             # 叠加后组合日收益(用于实现波动率)
    scaled_hist = []

    for i, d in enumerate(cal):
        if i > 0:
            prev_w = scaled_hist[i - 1]
            day_ret = float((prev_w * rets.loc[d]).sum())
            eq *= (1 + day_ret)
            ret_hist.append(day_ret)
        peak = max(peak, eq)
        dd = eq / peak - 1

        # ---- 状态机（用当日收盘可知的 dd 决定下一日缩放）----
        if state == "normal":
            if dd <= dd_hard:
                state = "flat"
            elif dd <= dd_soft:
                state = "reduced"
        else:  # reduced / flat
            if dd > recover_above:
                state = "normal"
            elif dd <= dd_hard:
                state = "flat"
            elif dd > dd_soft:
                state = "reduced"
        scale = 1.0 if state == "normal" else (soft_scale if state == "reduced" else 0.0)

        # ---- 波动率目标 ----
        if vol_target is not None and len(ret_hist) >= 20:
            rv = float(np.std(ret_hist[-vol_window:]) * np.sqrt(250))
            if rv > vol_target:
                scale *= max(vol_target / rv, 0.2)

        w_today = w_df.loc[d].astype(float)
        w_scaled = w_today * scale

        # 缩放带来的额外换手成本（相对上一日已缩放权重）
        if i > 0:
            extra_turnover = float((w_scaled - scaled_hist[i - 1]).abs().sum())
            # w 本身的换手成本已由原回测计入, 只对缩放引起的差异近似计费
            base_turnover = float((w_today - w_df.loc[cal[i - 1]]).abs().sum())
            extra = max(extra_turnover - base_turnover, 0.0)
            if extra > 0:
                eq *= (1 - extra * cost_rate)
        eq_curve[i] = eq
        scaled_hist.append(w_scaled)

    equity_df = pd.DataFrame({"equity": eq_curve}, index=cal)
    w_out = pd.DataFrame(scaled_hist, index=cal, columns=sectors)
    metrics = compute_metrics(equity_df, None, rets)
    return {"equity": equity_df, "weights": w_out, "metrics": metrics}


def compute_metrics(equity_df: pd.DataFrame, trades_df: pd.DataFrame, rets: pd.DataFrame):
    eq = equity_df["equity"]
    n_years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total_ret = float(eq.iloc[-1] / eq.iloc[0] - 1)
    cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1 / n_years) - 1)
    daily = eq.pct_change().dropna()
    vol = float(daily.std() * np.sqrt(250))
    sharpe = float(daily.mean() / daily.std() * np.sqrt(250)) if daily.std() > 0 else 0.0
    dd = float((eq / eq.cummax() - 1).min())

    m = {
        "total_return": total_ret,
        "cagr": cagr,
        "ann_vol": vol,
        "sharpe": sharpe,
        "max_drawdown": dd,
        "n_trades": 0, "win_rate": np.nan, "avg_trade_ret": np.nan,
        "avg_hold_days": np.nan, "best_trade": np.nan, "worst_trade": np.nan,
    }
    if trades_df is not None and len(trades_df) > 0:
        m["n_trades"] = len(trades_df)
        m["win_rate"] = float((trades_df["ret"] > 0).mean())
        m["avg_trade_ret"] = float(trades_df["ret"].mean())
        m["avg_hold_days"] = float(trades_df["days"].mean())
        m["best_trade"] = float(trades_df["ret"].max())
        m["worst_trade"] = float(trades_df["ret"].min())
    return m


def benchmark_equal_weight(dfs: dict, start=None, end=None):
    """基准1：所有板块等权买入持有。"""
    all_dates = sorted(set().union(*[set(df.index) for df in dfs.values()]))
    cal = pd.DatetimeIndex(all_dates)
    if start:
        cal = cal[cal >= pd.Timestamp(start)]
    if end:
        cal = cal[cal <= pd.Timestamp(end)]
    closes = pd.DataFrame({name: df["close"] for name, df in dfs.items()}).reindex(cal).ffill()
    rets = closes.pct_change().fillna(0.0)
    daily = rets.mean(axis=1)
    eq = (1 + daily).cumprod()
    return pd.DataFrame({"equity": eq}, index=cal)


def benchmark_index(bench_close: pd.Series, dfs: dict, start=None, end=None):
    """基准2：沪深300 买入持有（与组合同区间）。"""
    all_dates = sorted(set().union(*[set(df.index) for df in dfs.values()]))
    cal = pd.DatetimeIndex(all_dates)
    if start:
        cal = cal[cal >= pd.Timestamp(start)]
    if end:
        cal = cal[cal <= pd.Timestamp(end)]
    s = bench_close.reindex(cal).ffill()
    eq = s / s.iloc[0]
    return pd.DataFrame({"equity": eq}, index=cal)
