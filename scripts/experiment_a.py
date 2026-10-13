# -*- coding: utf-8 -*-
"""实验A: 三路径候选 vs 基线(温度+回撤)。指标: 信号事件级 Precision(有效启动标签)/Recall(漏检谷底覆盖)。
事件=信号起点(>60日折叠)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from cycle_labels import troughs
from reversal_detector import detect, effective_start

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]
ref = pd.read_csv(os.path.join(config.DATA_DIR, "idx_hs300.csv"), parse_dates=["date"]).set_index("date")["close"].astype(float)

rows = []
sig_all = {"base": 0, "A": 0, "B": 0, "C": 0, "fast": 0}
prec = {"base": [], "A": [], "B": [], "C": [], "fast": []}
for name, d in dfs.items():
    c = d["close"].dropna()
    c.index = pd.to_datetime(c.index)
    c = c[c.index >= "2010-01-01"]
    if len(c) < 1250:
        continue
    br = d["breadth"] if "breadth" in d else None
    if br is not None:
        br.index = pd.to_datetime(br.index)
        br = br.reindex(c.index)
    det = detect(c, ref.reindex(c.index), br, d["amount"] if "amount" in d else None)
    lab = effective_start(c)
    cyc, dd = troughs(c)

    def events(mask):
        idx = [i for i in range(len(c)) if mask[i]]
        return [s for k, s in enumerate(idx) if k == 0 or s - idx[k - 1] > 60]

    ev_base = events([np.isfinite(dd[i]) and (((det["watch"].values[i] * 0) == 0) and False or False) for i in range(len(c))])  # placeholder
    # 基线事件: temp<12 ∨ 自适应回撤(与capture口径一致)
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    temp = (100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)).values
    dep = pd.Series(-dd).rolling(1250, min_periods=250).max().values
    thr = np.maximum(0.30, 0.70 * dep)
    ev_base = events([np.isfinite(dd[i]) and ((np.isfinite(temp[i]) and temp[i] < 12) or dd[i] <= -thr[i]) for i in range(len(c))])
    ev_A = events((det["start"] * det["pathA"]).values)
    ev_B = events(det["pathB"].values)
    ev_C = events(det["pathC"].values)
    fast = events(((det["start"] * det["pathA"] * 0 + (det["start"] & (det["watch"] > 0))).values * 0 + 0))  # fast已在pathA内
    grp = {"base": ev_base, "A": ev_A, "B": ev_B, "C": ev_C}
    lv = lab.values
    for k, evs in grp.items():
        sig_all[k] += len(evs)
        for s in evs:
            if np.isfinite(lv[s]):
                prec[k].append(int(lv[s]))
    # 漏检谷底(基线未覆盖±60日)被三路径覆盖数
    for t0 in cyc:
        if not any(abs(s - t0) <= 60 for s in ev_base):
            hit = sum(1 for k in ("A", "B", "C") if any(abs(s - t0) <= 60 for s in grp[k]))
            if hit:
                rows.append((name, str(c.index[t0].date()), hit))

P = {k: (f"{np.mean(v)*100:.0f}%" if v else "-") for k, v in prec.items()}
print("信号事件数:", sig_all)
print(f"有效启动Precision(事件级): base={P['base']} A(V型)={P['A']} B(U型)={P['B']} C(浅调)={P['C']}")
new_cov = pd.DataFrame(rows, columns=["sector", "trough", "paths"])
print(f"\n基线漏检谷底被三路径新增覆盖: {new_cov.sector.nunique() and len(new_cov)}轮")
print(new_cov.groupby('paths').size().to_string())
