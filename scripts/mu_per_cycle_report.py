# -*- coding: utf-8 -*-
"""审查七轮: MU九轮周期终报告 — A(逆势分批)与E(突破顺势加仓)两冠军配置的逐轮明细。
真实增量份额/费用/现金流口径(引擎已修正), 每轮: 入场/出场/持有/投入/加仓次数/收益/捕获/回吐。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from engine import run_specialist
from scripts.mu_af_v2 import v4, brk, trend_add_sig, ext85, ext_tb, c, cal, cycles  # 复用已验证信号

BUDGET = 100.0


def state_signals(esig, asig, ext, brk_as_entry=False):
    pos, adds = 0, 0
    entry_s, add_s = np.zeros(len(cal)), np.zeros(len(cal))
    ev = ext.reindex(cal).fillna(0).values
    es, as_ = esig.reindex(cal).fillna(0).values, asig.reindex(cal).fillna(0).values
    for i in range(len(cal)):
        if pos and ev[i] >= 1:
            pos, adds = 0, 0
        elif pos == 0 and (es[i] >= 1 or (brk_as_entry and as_[i] >= 1)):
            pos, adds, entry_s[i] = 1, 0, 1.0
        elif pos and adds < 2 and as_[i] >= 1:
            add_s[i], adds = 1.0, adds + 1
    return pd.Series(entry_s, index=cal), pd.Series(add_s, index=cal)


def per_cycle(fills):
    trips, cur = [], None
    for f in fills:
        d, side, sh, px = pd.Timestamp(f[0]), f[1], f[2], f[3]
        if side in ("BUY", "ADD"):
            if cur is None:
                cur = dict(first=d, spend=0.0, shares=0.0, n_add=0)
            cur["spend"] += sh * px
            cur["shares"] += sh
            if side == "ADD":
                cur["n_add"] += 1
        elif side == "SELL" and cur is not None:
            cur["exit"], cur["exit_px"] = d, px
            trips.append(cur)
            cur = None
    if cur is not None:
        trips.append(cur)
    out = {}
    for t in trips:
        end = t.get("exit") or cal[-1]
        best, bov = None, -1
        for _, r in cycles.iterrows():
            a = pd.Timestamp(r.start) - pd.Timedelta(days=180)
            b = pd.Timestamp(r.end) + pd.Timedelta(days=250)
            ov = (min(pd.Timestamp(end), b) - max(t["first"], a)).days
            if ov > bov:
                best, bov = r, ov
        if bov <= 0:
            continue
        i0, i1 = cal.get_indexer([pd.Timestamp(best.start)])[0], cal.get_indexer([pd.Timestamp(best.end)])[0]
        i_e = cal.get_indexer([t["first"]])[0]
        i_x = cal.get_indexer([end])[0]
        avg = t["spend"] / t["shares"]
        exit_px = t.get("exit_px") or float(c.iloc[-1])
        ref = float(c.iloc[i1] / c.iloc[i0] - 1)
        pxp = c.iloc[i_e:i_x + 1].dropna().values
        cash_out = t["spend"] / 0.999  # 审查十轮: spend是费后证券额, 现金支出=费前全额
        ret_acct = (t["shares"] * exit_px * 0.999 - cash_out) / BUDGET  # 周期账户贡献(含买入费用, 预算100基准)
        out[best.cycle_id] = dict(entry=t["first"].date(), exit=pd.Timestamp(end).date(),
                                  hold=int(i_x - i_e), n_add=t["n_add"], spend=round(t["spend"], 1),
                                  ret=round(exit_px / (avg * 1.001) - 1, 3), ret_acct=round(float(ret_acct), 3),
                                  mfe=round(float(pxp.max() / avg - 1), 2) if len(pxp) else np.nan,
                                  mae=round(float(pxp.min() / avg - 1), 2) if len(pxp) else np.nan,
                                  capture=round((exit_px / avg - 1) / ref, 2) if ref > 0 else np.nan,
                                  giveback=round((float(pxp.max()) - exit_px) / float(pxp.max()), 2) if len(pxp) and pxp.max() > 0 else np.nan,
                                  closed=int(t.get("exit") is not None))
    return out


for tag, esig, asig, ext, tr in [("A 逆势分批", v4, None, ext85, (0.30, 0.35, 0.35)),
                                 ("E 突破顺势加仓", v4, brk, ext85, (0.30,))]:
    if asig is None:
        entry_sig, add_sig = esig, None
    else:
        entry_sig, add_sig = state_signals(esig, asig, ext)
    eq, fills, _ = run_specialist(c, entry_sig.reindex(cal).fillna(0), ext.reindex(cal).fillna(0), cal,
                                  budget=BUDGET, cost=0.001, tranches=tr,
                                  add_sig=add_sig, add_frac=0.35, max_adds=2)
    rep = per_cycle(fills)
    print(f"\n=== {tag} ===")
    for cid, r in rep.items():
        print(f"{cid}: 入{r['entry']} 出{r['exit']} 持{r['hold']}日 加仓{r['n_add']}次 "
              f"投入{r['spend']} 持仓收益{r['ret']*100:+.0f}% 账户贡献{r['ret_acct']*100:+.0f}% "
              f"MFE{r['mfe']*100:+.0f}% MAE{r['mae']*100:+.0f}% "
              f"捕获{r['capture']*100:.0f}% 回吐{r['giveback']*100:.0f}%" + ("" if r["closed"] else " [未平仓]"))


