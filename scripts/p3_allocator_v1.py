# -*- coding: utf-8 -*-
"""审查十五轮P3: 分配器框架(冻结首版) — 机会评分=同规则回测终值/|MDD|比值(S2口径: MU 231, WDC 15),
权重∝评分归一, 集中度可选35/60/80/100%(首版测80/20与100/0对照)。组合=两标的各自S2权益按权重日再平衡近似。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features


def s2_equity(fn):
    c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    ev = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
    pos = pd.Series(ev, index=c.index).rolling(120, min_periods=1).max().astype(float).shift(1).fillna(0.0)
    ret = c.pct_change().fillna(0.0)
    return 100.0 * (1 + pos * ret).cumprod()


eq_mu = s2_equity("MU.csv")
eq_wdc = s2_equity("WDC.csv")
idx = eq_mu.index.union(eq_wdc.index)
eq_mu = eq_mu.reindex(idx).ffill().fillna(100.0)
eq_wdc = eq_wdc.reindex(idx).ffill().fillna(100.0)
score_mu, score_wdc = 231.0, 15.0  # 冻结输入: 上轮同规则比值
w_mu_raw = score_mu / (score_mu + score_wdc)
print(f"评分归一: MU={w_mu_raw:.0%} WDC={1 - w_mu_raw:.0%} (原始比值231/15)")
for w in (1.0, min(0.8, w_mu_raw), 0.6, 0.35):
    port = w * eq_mu + (1 - w) * eq_wdc
    mdd = float((port / port.cummax() - 1).min())
    print(f"MU{w:.0%}+WDC{1 - w:.0%}: 终值={float(port.iloc[-1]):.1f} MDD={mdd:.0%} 比值={float(port.iloc[-1]) / abs(mdd):.0f}")
