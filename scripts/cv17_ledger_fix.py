# -*- coding: utf-8 -*-
"""审查三十四轮: P0账本守恒重构 — 唯一目标仓位执行器(nav基准, 现金守恒, 费用为权益唯一变化源),
确定性单元测试(连续复利/半仓现金保留/减半后清仓现金保留), 重跑V0/V1/V2并撤回旧结果, V2拆解消融(V2a仅减半/V2b仅清仓/V2双动作)。"""
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


# ===== 唯一执行器: 目标仓位×当前NAV, 现金守恒 =====
def execute(cash, shares, p, level):
    nav = cash + shares * p
    tgt = level * nav
    cur = shares * p
    fee = 0.0
    if tgt < cur - 1e-12:
        sell_sh = (cur - tgt) / p
        fee += sell_sh * p * COST
        cash += sell_sh * p * (1 - COST)
        shares -= sell_sh
    elif tgt > cur + 1e-12 and cash > 1e-12:
        buy_cash = min(tgt - cur, cash)
        fee += buy_cash * COST
        shares += buy_cash * (1 - COST) / p
        cash -= buy_cash
    return cash, shares, fee


def run_variant(mode):
    """V0=orig V1=半仓 V2a=仅减半 V2b=仅清仓 V2=双动作。"""
    cash, shares, level = 100.0, 0.0, 0.0
    in_pos = False
    entry_i = -999
    entry_px = np.nan
    eq = pd.Series(np.nan, index=c.index)
    fees = 0.0
    trades = []  # [entry_i, exit_i, entry_fill_px, exit_fill_px, levels_seen, realized_pnl]
    log = []
    expo_sum = expo_n = 0
    for i in range(1, n):
        p = pxs[i]
        sd = i - 1
        new_level = None
        if mode == "V0":
            if not in_pos and sd in evset:
                new_level = 1.0
            elif in_pos and sd - entry_i >= HOLD:
                new_level = 0.0
        elif mode == "V1":
            if not in_pos and sd in evset:
                new_level = 0.5
            elif in_pos and sd - entry_i >= HOLD:
                new_level = 0.0
        else:  # V2家族
            if not in_pos and sd in evset:
                new_level, entry_i, entry_px = 1.0, sd, np.nan
            elif in_pos:
                broke = pxs[sd] < ma100[sd]
                deep = pxs[sd] < entry_px * 0.80
                if mode in ("V2b", "V2") and broke and deep and level > 0:
                    log.append((c.index[i].date(), "INVALIDATED清仓"))
                    new_level = 0.0
                elif mode in ("V2a", "V2") and broke and level > 0.5:
                    log.append((c.index[i].date(), "DETERIORATING减半"))
                    new_level = 0.5
                elif sd - entry_i >= HOLD:
                    new_level = 0.0
        if new_level is not None:
            if new_level == 0.0 and in_pos:
                trades[-1][1] = i
                trades[-1][3] = p
            level = new_level
        nav_before = cash + shares * p
        cash, shares, fee = execute(cash, shares, p, level)
        fees += fee
        if level > 0 and not in_pos and shares > 0:
            in_pos = True
            entry_i = sd
            entry_px = p
            trades.append([i, None, p, None, set([level]), 0.0])
        elif in_pos and shares <= 1e-12 and level == 0.0:
            in_pos = False
        if in_pos and level < 1.0:
            trades[-1][4].add(level)
        eq.iloc[i] = cash + shares * p
        expo_sum += shares * p / eq.iloc[i] if eq.iloc[i] > 0 else 0
        expo_n += 1
        # 资金守恒断言: 权益变化只能来自费用
        assert abs(eq.iloc[i] - (nav_before - fee)) < 1e-6
    for tr in trades:
        x2 = tr[1] if tr[1] is not None else n - 1
        tr[5] = float(min(pxs[tr[0]:x2 + 1]) / tr[2] - 1)  # MAE
    return eq.ffill().fillna(100.0), trades, log, fees, expo_sum / max(expo_n, 1)


# ===== P0 确定性单元测试 =====
print("=== P0 确定性单元测试 ===")
ok = True
# 测试A: 连续两笔盈利交易正确复利
cash, shares = 100.0, 0.0
cash, shares, _ = execute(cash, shares, 10.0, 1.0)
e1 = cash + shares * 10.0
cash, shares, _ = execute(cash, shares, 20.0, 0.0)   # 卖出
cash, shares, _ = execute(cash, shares, 20.0, 1.0)   # 再买
cash, shares, _ = execute(cash, shares, 30.0, 0.0)   # 卖出
expect = 100.0 * (20.0 / 10.0) * (30.0 / 20.0) * (1 - COST) ** 4
ok &= abs((cash + shares) - expect) < 1e-6
print(f"A 连续复利: 终值{cash + shares:.4f} 期望{expect:.4f} {'PASS' if abs((cash+shares)-expect)<1e-6 else 'FAIL'}")
# 测试B: 半仓入场现金保留
cash, shares = 100.0, 0.0
cash, shares, _ = execute(cash, shares, 10.0, 0.5)
conserved = abs((cash + shares * 10.0) - 100.0 * (1 - COST * 0.5)) < 1e-6
ok &= conserved and cash > 40
print(f"B 半仓现金保留: 持仓{shares * 10:.2f} 现金{cash:.2f} {'PASS' if conserved and cash > 40 else 'FAIL'}")
# 测试C: 减半后清仓, 现金=两次卖出所得, 无丢失
cash, shares = 100.0, 0.0
cash, shares, _ = execute(cash, shares, 10.0, 1.0)
cash, shares, _ = execute(cash, shares, 12.0, 0.5)   # 减半
mid_cash = cash
cash, shares, _ = execute(cash, shares, 12.0, 0.0)   # 清仓
total = cash
solo = 100.0 * (1 - COST) / 10.0 * 12.0 * (1 - COST)
ok &= abs(total - solo) < 1e-6
print(f"C 减半后清仓: 终现金{total:.4f} 单笔直出{solo:.4f} {'PASS' if abs(total-solo)<1e-6 else 'FAIL'}")
print(f"单元测试整体: {'PASS' if ok else 'FAIL'}")
assert ok

# ===== P0 重跑三方案+消融 =====
print("\n=== 重跑(V0/V1/V2a/V2b/V2, 守恒账本) ===")
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
CASES = [t for t in qual if c.index[t].year in (2011, 2016, 2020, 2025)]
res = {}
for mode in ("V0", "V1", "V2a", "V2b", "V2"):
    eq, trades, log, fees, avg_expo = run_variant(mode)
    res[mode] = (eq, trades, log)
    r = eq.pct_change().dropna()
    closed = [tr for tr in trades if tr[1] is not None]
    case_out = []
    for t in CASES:
        cov = [tr for tr in trades if tr[0] <= t + 240 and (tr[1] is None or tr[1] >= t)]
        if cov:
            tr = cov[0]
            case_out.append(f"{c.index[t].date().year}:MAE{tr[5]:.0%}")
        else:
            case_out.append(f"{c.index[t].date().year}:无仓")
    print(f"{mode}: 终值{float(eq.iloc[-1]):.0f} MDD{float((eq/eq.cummax()-1).min()):.0%} Sharpe{float(r.mean()/r.std()*np.sqrt(252)):.2f} 交易{len(trades)} 费用{fees:.1f} 平均暴露{avg_expo:.0%} 最深MAE{min(tr[5] for tr in closed):.0%}")
    print(f"   四案例: {' | '.join(case_out)}")
print(f"\nV2调仓日志{len(res['V2'][2])}条, V2a{len(res['V2a'][2])}条, V2b{len(res['V2b'][2])}条")
print("(旧87/44/99作废 — 资金重置缺陷已修复)")
