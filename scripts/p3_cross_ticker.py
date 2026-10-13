# -*- coding: utf-8 -*-
"""审查十五轮P3前提: 跨标的机会质量 — MU/STX/WDC同一套冻结规则: S1阶段1状态持有(dd≤-35%&pos1250≤0.35),
S2双路径A∪B事件持120日。T+1, 费0.15%。无MU特化参数, 公平比较机会质量。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features, rule_state

for name, fn in {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv"}.items():
    c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    st = rule_state(c)
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    pathA = ((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)).values
    pathB = ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)).values
    ev = (pathA | pathB)
    out = []
    for label, pos in [("S1阶段1状态持有", st.astype(float)), ("S2双路径持120日", pd.Series(ev, index=c.index).rolling(120, min_periods=1).max().astype(float))]:
        # T+1执行近似: 信号决定次日仓位
        pos1 = pos.shift(1).fillna(0.0)
        ret = c.pct_change().fillna(0.0)
        eq = 100.0 * (1 + pos1 * ret).cumprod()
        mdd = float((eq / eq.cummax() - 1).min())
        out.append(f"{label}: 终值{float(eq.iloc[-1]):.1f} MDD{mdd:.0%} 比值{float(eq.iloc[-1])/abs(mdd):.0f}")
    print(f"{name}: " + " | ".join(out))
