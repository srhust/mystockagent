# -*- coding: utf-8 -*-
"""审查十七轮: ①双路径账户回测改真120日限时(目标仓位, run_position_ledger真退出)
②Fast-V v1.0冻结版账本指标重算(PF=净盈亏口径) MU/STX/WDC清洁数据。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features, build_industry_features
from p2_exit_rerun import run_position_ledger
import mu_af_v2 as M

c = M.c
f = build_features(c, [])
dd = f["dd252"]
ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
pathA = ((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)).values
pathB = ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)).values
ev = (pathA | pathB)

print("=== 双路径账户: 真120日限时 ===")
pos120 = pd.Series(ev.astype(float), index=c.index).rolling(120, min_periods=1).max()
eq, led = run_position_ledger(c, pos120)
L = pd.DataFrame(led, columns=["d", "ret", "hold"])
mdd = float((eq / eq.cummax() - 1).min())
pf = L[L.ret > 0].ret.sum() / abs(L[L.ret <= 0].ret.sum()) if len(L) and (L.ret <= 0).any() else np.inf
print(f"限时120日: 终值{float(eq.iloc[-1]):.1f} MDD{mdd:.0%} 笔{len(L)} 胜率{(L.ret>0).mean():.0%} PF{pf:.2f} (原462.68/-61.8%作废)")

print("\n=== Fast-V v1.0 账本重算(净利口径) ===")
cost = 0.0015
for name, fn in {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv"}.items():
    cc = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    ff = build_features(cc, [])
    ddx = ff["dd252"]
    m5, m10 = cc.rolling(5).mean(), cc.rolling(10).mean()
    v20 = cc.pct_change().rolling(20).std()
    st250 = (ddx <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    vel = (ddx.diff(10) <= -0.15).values
    fpath = os.path.join(config.DATA_DIR, "mu", f"{name.lower()}_fund.csv")
    if os.path.exists(fpath) and os.path.getsize(fpath) > 100:
        ind = build_industry_features(fpath, cc.index)
        fund_ok = (ind["rev_yoy"] > -0.30).values
    else:
        fund_ok = np.ones(len(cc), bool)
    n = len(cc)
    pxs = cc.values
    cash, shares, in_pos, pend = 100.0, 0.0, False, None
    entry_px = stop_px = np.nan
    entry_idx, crash_start, last_end = -1, None, -999
    eqf = pd.Series(np.nan, index=cc.index)
    led2 = []
    for i in range(1, n):
        px = pxs[i]
        if pend is not None:
            kind, d2 = pend
            pdx = pxs[d2]
            if kind == "buy":
                shares = cash * (1 - cost) / pdx
                entry_px, entry_idx = pdx, d2
                stop_px = pdx * (1 - 3 * float(v20.iloc[d2]))
                cash = 0.0
                in_pos = True
            else:
                cash = shares * pdx * (1 - cost)
                led2.append((cc.index[d2], pdx / entry_px - 1, d2 - entry_idx))
                shares = 0.0
                in_pos = False
                last_end = d2
                crash_start = None
            pend = None
        if vel[i] and not in_pos and pend is None and crash_start is None and i - last_end > 90:
            crash_start = i
        if crash_start is not None and not in_pos and pend is None and i - crash_start <= 15:
            if px > m10.iloc[i] and i - crash_start >= 2 and fund_ok[i]:
                pend = ("buy", i + 1)
        elif crash_start is not None and not in_pos and i - crash_start > 15:
            crash_start = None
        if in_pos and pend is None and (px / entry_px - 1 >= 0.15 or px <= stop_px or i - entry_idx >= 60):
            pend = ("sell", i + 1)
        eqf.iloc[i] = cash + shares * px
    eqf = eqf.ffill().fillna(100.0)
    L2 = pd.DataFrame(led2, columns=["date", "ret", "hold"])
    if len(L2):
        wr = (L2.ret > 0).mean()
        pf = L2[L2.ret > 0].ret.sum() / abs(L2[L2.ret <= 0].ret.sum()) if (L2.ret <= 0).any() else np.inf
        aw = L2[L2.ret > 0].ret.mean()
        al = L2[L2.ret <= 0].ret.mean()
        mdf = float((eqf / eqf.cummax() - 1).min())
        print(f"{name}: 笔{len(L2)} 胜率{wr:.0%} 均赢{aw:+.1%}/均亏{al:+.1%} PF{pf:.2f} 中位持仓{L2.hold.median():.0f}日 终值{float(eqf.iloc[-1]):.1f} MDD{mdf:.0%}")
        print("   明细:", [(str(d.date()), f'{r:+.1%}') for d, r, _ in led2])
