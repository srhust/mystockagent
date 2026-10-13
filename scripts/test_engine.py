# -*- coding: utf-8 -*-
"""周期引擎逻辑测试：合成周期数据 → 温度/阶段验证。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from cycle_system.cycle_engine import enrich_sector, stage_transitions  # noqa: E402
from cycle_system.strategy import position_target, apply_trailing_stop  # noqa: E402
from cycle_system import analytics  # noqa: E402


def synth_sector(n=3000, seed=42):
    """合成一个带 4 轮完整周期的板块（周期 ~750 交易日）。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    # 周期因子: 正弦 + 轻微趋势
    cycle = 0.6 * np.sin(2 * np.pi * t / 750 - np.pi / 2)  # 从底部开始
    drift = 0.15 * t / n
    noise = rng.normal(0, 0.008, n).cumsum() * 0.15
    logp = cycle + drift + noise
    close = np.exp(logp) * 100
    high = close * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.008, n)))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    volume = rng.lognormal(10, 0.3, n)
    # 量能与周期正相关
    volume = volume * (1.2 + 0.8 * cycle)
    amount = volume * close
    dates = pd.bdate_range("2010-01-01", periods=n)
    df = pd.DataFrame({
        "date": dates, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume, "amount": amount,
    })
    return df


def main():
    df = synth_sector()
    bench = pd.Series(np.exp(np.cumsum(np.random.default_rng(1).normal(0.0002, 0.01, len(df)))) * 3000,
                      index=df["date"])

    # 个股宽表: 与板块指数相关
    stock_closes = pd.DataFrame({
        f"s{i}": df["close"].values * (1 + np.random.default_rng(i).normal(0, 0.005, len(df)).cumsum() * 0.05)
        for i in range(8)
    }, index=df["date"])

    # PB 序列: 与价格强相关（净资产缓慢增长）
    pb = pd.Series(close_pb := df["close"].values / (df["close"].mean() * 1.5 / 2.0), index=df["date"])

    e = enrich_sector(df, bench, stock_closes=stock_closes, pb_series=pb)
    print("列:", [c for c in e.columns])
    print(f"温度范围: [{e['temp'].min():.1f}, {e['temp'].max():.1f}], NaN数: {e['temp'].isna().sum()}")

    # 阶段分布
    print("\n阶段分布:")
    print(e["stage"].value_counts().to_string())

    # 在合成周期底部/顶部位置的温度（周期750天, 相位-pi/2: t=0 是底, t=375 是顶）
    for label, pos in [("底部附近(t=750)", 750), ("上升中(t=930)", 930), ("顶部附近(t=1125)", 1125),
                       ("下降中(t=1310)", 1310)]:
        row = e.iloc[pos]
        print(f"{label}: 温度={row['temp']:.0f} 阶段={row['stage']} "
              f"PB分位={row.get('pb_pct', np.nan):.2f}")

    # 阶段切换事件
    tr = stage_transitions(e["stage"])
    print(f"\n阶段切换事件数: {len(tr)}")
    starts = tr[tr["to"] == "recovery"]
    print(f"启动事件日期: {[str(d.date()) for d in starts['date'].iloc[:8]]}")

    # 策略仓位
    pos = position_target(e, mode="right")
    pos_ts = apply_trailing_stop(e, pos)
    print(f"\n右侧策略持仓天数占比: {pos.mean():.2f}, 止损后: {pos_ts.mean():.2f}")

    # 分析表
    st = analytics.stage_return_table(e, level="episode")
    print("\n片段级阶段收益表:")
    print(st.to_string(index=False))
    tb = analytics.temperature_bucket_table(e)
    print("\n温度分桶(60日):")
    print(tb[tb["horizon"] == 60][["stage", "n", "win_rate", "mean_ret"]].to_string(index=False))

    ups, downs = analytics.zigzag_cycle_stats(e)
    print(f"\nzigzag 上行段: {len(ups)} 轮")
    for u in ups[:5]:
        print(f"  {u['bottom'].date()} -> {u['top'].date()}: {u['ret'] * 100:+.0f}% / {u['days']}天")

    print("\nTEST DONE")


if __name__ == "__main__":
    main()
