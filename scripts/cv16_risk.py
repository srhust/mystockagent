# -*- coding: utf-8 -*-
"""审查三十三轮: ①P0 归属模型统一(entry_trade_id/crossing_trade_id/association_type/linked, MAE用T+1成交价截断至真实退出)
②P1 穿越态逐日诊断(2011/2016/2020/2025: 恶化信号时点vs实际MAE) ③P1 风险状态机最小对照:
V0=orig120日 V1=固定半仓 V2=状态驱动(DETERIORATING破MA100减半→INVALIDATED破MA100且低于成交成本20%清仓, 120日兜底)。"""
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
import mu_af_v2 as M

D = os.path.join(config.DATA_DIR, "mu")
COST = 0.0015
HOLD, DEDUP = 120, 60
c = pd.read_csv(os.path.join(D, "MU.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
f = build_features(c, [])
dd = f["dd252"]
ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
dp = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
v4tr = (M.v4.reindex(c.index).fillna(0) > 0) & (M.v4.reindex(c.index).fillna(0).diff() > 0)
raw = dp.values | v4tr.values
n = len(c)
pxs = c.values
ma100 = c.rolling(100).mean().values

evd, last = [], -9999
for i in range(n):
    if raw[i] and i - last >= DEDUP:
        evd.append(i)
        last = i
evset = set(evd)


def run_variant(mode):
    """mode: V0(orig) / V1(半仓) / V2(状态机)。返回(逐日权益, 交易列表, 状态日志)。"""
    cash, shares = 100.0, 0.0
    level = 0.0  # 目标暴露 0/0.5/1
    in_pos = False
    entry_i = -999
    entry_fill_px = np.nan
    eq = pd.Series(np.nan, index=c.index)
    trades = []
    log = []
    for i in range(1, n):
        p = pxs[i]
        sd = i - 1
        # 决策(基于昨日收盘可见信息, T+1执行=今日)
        if mode == "V0":
            if not in_pos and sd in evset:
                level = 1.0 if not in_pos else level
            elif in_pos and i - 1 - entry_i >= HOLD:
                level = 0.0
        elif mode == "V1":
            if not in_pos and sd in evset:
                level = 0.5
            elif in_pos and i - 1 - entry_i >= HOLD:
                level = 0.0
        else:  # V2
            if not in_pos and sd in evset:
                level, entry_i = 1.0, i - 1
            elif in_pos:
                age = i - 1 - entry_i
                broke = pxs[sd] < ma100[sd]
                deep = pxs[sd] < entry_fill_px * 0.80
                if broke and deep:
                    if level > 0:
                        log.append((c.index[i].date(), "INVALIDATED清仓", f"{pxs[sd]:.2f}<MA100且<成本80%"))
                    level = 0.0
                elif broke and level > 0.5:
                    log.append((c.index[i].date(), "DETERIORATING减半", f"{pxs[sd]:.2f}<MA100"))
                    level = 0.5
                elif age >= HOLD:
                    level = 0.0
        # 执行(T+1已含在当日): 调整份额至level×100预算
        tgt_sh = level * 100.0 * (1 - COST) / p if level > 0 else 0.0
        if not in_pos and level > 0:
            shares, cash, in_pos = tgt_sh, 0.0, True
            entry_i = i - 1
            entry_fill_px = p
            trades.append([i, None, p, level])
        elif in_pos and level < 1.0 and level > 0 and shares > tgt_sh * 1.0001:
            sell = shares - tgt_sh
            cash += sell * p * (1 - COST)
            shares = tgt_sh
        elif in_pos and level == 0.0:
            cash = shares * p * (1 - COST)
            trades[-1][1] = i
            trades[-1].append(p)  # 退出成交价
            shares, in_pos = 0.0, False
        eq.iloc[i] = cash + shares * p
    return eq.ffill().fillna(100.0), trades, log


print("=== P0 归属统一(V0基准账本, 交易ID贯穿) ===")
eq0, trades0, _ = run_variant("V0")
pivots = []
hi = lo = pxs[0]
trend = 0
lo_i = 0
for i in range(1, n):
    x = pxs[i]
    if trend >= 0:
        if x > hi:
            hi = x
        elif x <= hi * 0.70:
            pivots.append(("H", i))
            trend, lo, lo_i = -1, x, i
    else:
        if x < lo:
            lo, lo_i = x, i
        elif x >= lo * 1.30:
            pivots.append(("L", lo_i))
            trend, hi = 1, x
if trend < 0:
    pivots.append(("L", lo_i))
lows = sorted(set(i for k, i in pivots if k == "L"))
qual = [t for t in lows if n - (t + 1) >= 60 and float(pxs[t + 1:min(n, t + 121)].max() / pxs[t] - 1) >= 0.50]


def tmae(tr):
    ef, xf = tr[0], tr[1]
    x2 = xf if xf is not None else n - 1
    return float(min(pxs[ef:x2 + 1]) / tr[2] - 1)


for t in qual:
    cross = [k for k, tr in enumerate(trades0) if tr[0] <= t and (tr[1] is None or tr[1] > t)]
    newe = [k for k, tr in enumerate(trades0) if tr[0] > t and tr[0] <= t + 240]
    linked = sorted(set(cross) | set(newe))
    if cross:
        tr = trades0[cross[0]]
        x2 = tr[1] if tr[1] is not None else n - 1
        ret = (tr[4] if len(tr) > 4 else pxs[x2]) / tr[2] * (1 - COST) ** 2 - 1
        print(f"谷{c.index[t].date()}: CROSSING trade#{cross[0]}(入{c.index[tr[0]].date()} 成交价{tr[2]:.2f}, MAE{tmae(tr):.0%}, 净{ret:+.0%}) linked={linked}")
    elif newe:
        tr = trades0[newe[0]]
        x2 = tr[1] if tr[1] is not None else n - 1
        ret = (tr[4] if len(tr) > 4 else pxs[x2]) / tr[2] * (1 - COST) ** 2 - 1
        print(f"谷{c.index[t].date()}: NEW_ENTRY trade#{newe[0]}(成交{c.index[tr[0]].date()} 谷后{tr[0]-t}日, MAE{tmae(tr):.0%}, 净{ret:+.0%}) linked={linked}")
    else:
        print(f"谷{c.index[t].date()}: MISSED linked=[]")

print("\n=== P1 风险状态机对照(MU单标的同账本) ===")
CASES = [t for t in qual if c.index[t].year in (2011, 2016, 2020, 2025)]
for mode in ("V0", "V1", "V2"):
    eq, trades, log = run_variant(mode)
    r = eq.pct_change().dropna()
    L = [(tr[0], tr[1], (tr[4] if len(tr) > 4 else pxs[tr[1] if tr[1] is not None else n - 1]) / tr[2] * (1 - COST) ** 2 - 1, tmae(tr), tr[3]) for tr in trades]
    wins = sum(1 for _, _, ret, _, _ in L if ret > 0)
    closed = [x for x in L if x[1] is not None]
    case_out = []
    for t in CASES:
        cov = [x for x in L if x[0] <= t + 240 and (x[1] is None or x[1] >= t)]
        if cov:
            x0 = cov[0]
            case_out.append(f"{c.index[t].date().year}:MAE{x0[3]:.0%}/净{x0[2]:+.0%}/仓{x0[4]}")
        else:
            case_out.append(f"{c.index[t].date().year}:无仓")
    print(f"{mode}: 终值{float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} 胜{wins}/{len(closed)} 最深MAE{min(x[3] for x in closed):.0%} 调仓{len(log)}次")
    print(f"   四案例: {' | '.join(case_out)}")
eq2, _, log2 = run_variant("V2")
print("V2状态日志(全部):")
for d, a, why in log2:
    print(f"  {d} {a} ({why})")
