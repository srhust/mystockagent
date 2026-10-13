# -*- coding: utf-8 -*-
"""审查十五轮P1b: Fast-V出场消融(入场冻结E2=MA10收复) — X1现行(TP15%/3σ/60日) X2结构止损(入场20日低点)/TP15% X3分批止盈(+10%出半/+20%或3σ出清) X4=X1去限时。
逐笔账本(收益率口径)入CSV。"""
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


def sim(c, f, fund_ok, mode):
    dd = f["dd252"]
    ma10 = c.rolling(10).mean()
    vol20 = c.pct_change().rolling(20).std()
    low20 = c.rolling(20).min()
    vel = (dd.diff(10) <= -0.15).values
    n = len(c)
    cash, shares, in_pos, pend = 100.0, 0.0, False, None
    entry_px = stop_px = np.nan
    entry_idx, crash_start, last_end = -1, None, -999
    eq = pd.Series(np.nan, index=c.index)
    pxs = c.values
    mas, vols, lows = ma10.values, vol20.values, low20.values
    ledger = []
    half_out = False
    entry_frac = 1.0
    for i in range(1, n):
        px = pxs[i]
        if pend is not None:
            kind, d = pend
            pdx = pxs[d]
            if kind == "buy":
                shares = cash * (1 - cost) / pdx
                entry_px = pdx
                stop_px = pdx * (1 - 3 * float(vols[d])) if mode != "X2" else float(lows[d])
                entry_idx = d
                cash = 0.0
                in_pos = True
                half_out = False
                entry_frac = 1.0
            else:
                cash += shares * pdx * (1 - cost)
                ledger.append((c.index[d].date(), pdx / entry_px - 1, d - entry_idx))
                shares = 0.0
                in_pos = False
                last_end = d
                crash_start = None
            pend = None
        if vel[i] and not in_pos and pend is None and crash_start is None and i - last_end > 90:
            crash_start = i
        if crash_start is not None and not in_pos and pend is None and i - crash_start <= 15:
            if pxs[i] > mas[i] and i - crash_start >= 2 and fund_ok[i]:
                pend = ("buy", i + 1)
        elif crash_start is not None and not in_pos and i - crash_start > 15:
            crash_start = None
        if in_pos and pend is None:
            ret = px / entry_px - 1
            holding = i - entry_idx
            exit_all = ret >= (0.20 if mode == "X3" else 0.15) or px <= stop_px or (mode != "X4" and holding >= 60)
            if mode == "X3" and not half_out and ret >= 0.10:
                sell_sh = shares * 0.5
                cash += sell_sh * px * (1 - cost)
                shares -= sell_sh
                half_out = True
            if exit_all and shares > 0:
                pend = ("sell", i + 1)
        eq.iloc[i] = cash + shares * px
    eq = eq.dropna()
    mdd = float((eq / eq.cummax() - 1).min())
    L = pd.DataFrame(ledger, columns=["date", "ret", "hold"])
    wr = (L["ret"] > 0).mean() if len(L) else np.nan
    pf = L[L.ret > 0].ret.sum() / abs(L[L.ret <= 0].ret.sum()) if len(L) and (L.ret <= 0).any() else np.inf
    return dict(n=len(L), wr=f"{wr:.0%}" if len(L) else "NA", aw=f"{L[L.ret>0].ret.mean():+.1%}" if (L.ret > 0).any() else "NA",
                al=f"{L[L.ret<=0].ret.mean():+.1%}" if (L.ret <= 0).any() else "NA",
                pf=f"{pf:.2f}", hold=f"{L.hold.median():.0f}" if len(L) else "NA",
                final=f"{float(eq.iloc[-1]):.1f}", mdd=f"{mdd:.0%}"), L


rows = []
for name, fn in {"MU": ("MU.csv", "mu_fund.csv"), "STX": ("stx.csv", "stx_fund.csv"), "WDC": ("wdc.csv", "wdc_fund.csv")}.items():
    c = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn[0]), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    ind = build_industry_features(os.path.join(config.DATA_DIR, "mu", fn[1]), c.index)
    fund_ok = (ind["rev_yoy"] > -0.30).values
    print(f"\n===== {name} =====")
    for mode in ("X1", "X2", "X3", "X4"):
        r, L = sim(c, f, fund_ok, mode)
        rows.append(dict(stock=name, mode=mode, **r))
        print(f"{mode}: 笔{r['n']} 胜率{r['wr']} 均赢{r['aw']}/均亏{r['al']} PF={r['pf']} 持仓{r['hold']}日 终值{r['final']} MDD{r['mdd']}")
pd.DataFrame(rows).to_csv(os.path.join(config.DATA_DIR, "fastv_exit_ablation.csv"), index=False)
print("\n账本汇总已存 data/fastv_exit_ablation.csv")
