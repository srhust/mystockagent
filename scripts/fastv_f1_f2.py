# -*- coding: utf-8 -*-
"""审查十五轮P1: Fast-V F1/F2消融 — 入场变体(立即/MA5收复/MA10收复/连续两阳)×统一出场(TP15%/3σ止损/60日限时), MU/STX/WDC。
指标: 笔数/胜率/均赢均亏/盈亏比PF/中位持仓/终值/MDD。episode=急跌(dd十日-15pp)90日去重+15日有效期+基本面as-of过滤。"""
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

cost = 0.0015


def sim(c, f, fund_ok, entry_mode):
    dd = f["dd252"]
    ma5, ma10 = c.rolling(5).mean(), c.rolling(10).mean()
    vol20 = c.pct_change().rolling(20).std()
    vel = (dd.diff(10) <= -0.15).values
    n = len(c)
    cash, shares, in_pos, pend = 100.0, 0.0, False, None
    entry_px = stop_px = np.nan
    entry_idx, crash_start, last_end = -1, None, -999
    eq = pd.Series(np.nan, index=c.index)
    pxs = c.values
    mas5, mas10, vols = ma5.values, ma10.values, vol20.values
    wins, losses, holds, pf_w, pf_l = 0, 0, [], 0.0, 0.0
    entry_px_l = None
    for i in range(1, n):
        px = pxs[i]
        if pend is not None:
            kind, d = pend
            pdx = pxs[d]
            if kind == "buy":
                shares = cash * (1 - cost) / pdx
                entry_px = pdx
                stop_px = pdx * (1 - 3 * float(vols[d]))
                entry_idx = d
                cash = 0.0
                in_pos = True
                entry_px_l = pdx
            else:
                pnl = shares * pdx * (1 - cost)
                r = pdx / entry_px_l - 1
                cash = pnl
                (wins := wins)  # noqa
                if r > 0:
                    wins += 1
                    pf_w += pnl
                else:
                    losses += 1
                    pf_l += abs(pnl)
                holds.append(d - entry_idx)
                shares = 0.0
                in_pos = False
                last_end = d
                crash_start = None
            pend = None
        if vel[i] and not in_pos and pend is None and crash_start is None and i - last_end > 90:
            crash_start = i
        if crash_start is not None and not in_pos and pend is None and i - crash_start <= 15:
            ok = False
            if entry_mode == "E0" and i - crash_start >= 2:
                ok = True
            elif entry_mode == "E1" and pxs[i] > mas5[i] and i - crash_start >= 2:
                ok = True
            elif entry_mode == "E2" and pxs[i] > mas10[i] and i - crash_start >= 2:
                ok = True
            elif entry_mode == "E3" and i - crash_start >= 3 and pxs[i] > pxs[i - 1] > pxs[i - 2]:
                ok = True
            if ok and fund_ok[i]:
                pend = ("buy", i + 1)
        elif crash_start is not None and not in_pos and i - crash_start > 15:
            crash_start = None
        if in_pos and pend is None:
            holding = i - entry_idx
            if px / entry_px - 1 >= 0.15 or px <= stop_px or holding >= 60:
                pend = ("sell", i + 1)
        eq.iloc[i] = cash + shares * px
    eq = eq.dropna()
    mdd = float((eq / eq.cummax() - 1).min())
    nt = wins + losses
    pf = pf_w / pf_l if pf_l else float("inf")
    return dict(n=nt, win=f"{wins}/{nt}", wr=f"{wins/nt:.0%}" if nt else "NA",
                aw=f"{pf_w/max(wins,1):.1%}", al=f"{pf_l/max(losses,1):.1%}", pf=f"{pf:.2f}",
                hold=f"{np.median(holds):.0f}" if holds else "NA",
                final=f"{float(eq.iloc[-1]):.1f}", mdd=f"{mdd:.0%}")


for name, fn in {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv"}.items():
    c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    ind = build_industry_features(os.path.join(config.DATA_DIR, "mu", {"MU": "mu_fund.csv", "STX": "stx_fund.csv", "WDC": "wdc_fund.csv"}[name]), c.index)
    fund_ok = (ind["rev_yoy"] > -0.30).values
    print(f"\n===== {name} =====")
    for em in ("E0", "E1", "E2", "E3"):
        r = sim(c, f, fund_ok, em)
        print(f"{em}: 笔{r['n']} 胜{r['win']}({r['wr']}) 均赢{r['aw']}/均亏{r['al']} PF={r['pf']} 持仓{r['hold']}日 终值{r['final']} MDD{r['mdd']}")
