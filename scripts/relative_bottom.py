# -*- coding: utf-8 -*-
"""相对底部锚检验: 回撤触发型(-50%) vs 区间位置型(temp<12)。牧原 + 30行业。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402


def dd_events(c, th=-0.5, recov=-0.25, min_gap=120):
    """回撤从高点超th入场, 修复到recov或终点离场; min_gap防连续信号。"""
    dd = c / c.cummax() - 1.0
    cv, idx, dv = c.values, c.index, dd.values
    n, out, last = len(cv), [], -10**9
    i = 0
    while i < n:
        if np.isfinite(dv[i]) and dv[i] <= th and i - last > min_gap:
            j = i
            while j < n and dv[j] < recov:
                j += 1
            end = min(j, n - 1)
            fwd = cv[end] / cv[i] - 1.0
            out.append((idx[i].date(), idx[end].date(), fwd, end - i))
            last = j
            i = j + 1
        else:
            i += 1
    return out


def report(tag, trs):
    if not trs:
        print(f"{tag}: 0笔")
        return
    r = [x[2] for x in trs]
    print(f"{tag}: {len(trs)}笔 均值{np.mean(r) * 100:+.1f}%/中位{np.median(r) * 100:+.1f}% "
          f"胜率{sum(x > 0 for x in r)}/{len(r)} | " +
          " | ".join(f"{a}->{b} {v * 100:+.0f}%" for a, b, v, d in trs))


# 1) 牧原
df = pd.read_csv(r"E:\mystockagent\data\stock_002714.csv", parse_dates=["date"]).sort_values("date")
c = df.set_index("date")["close"].astype(float)
c = c[c.index >= "2015-01-01"]
report("牧原 回撤≤-50%(修复-25%出场)", dd_events(c))

# 2) 30行业: 回撤锚 vs 温度锚
obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]
all_e, all_r = [], []
for name, d in dfs.items():
    cs = d["close"].dropna()
    cs.index = pd.to_datetime(cs.index)
    cs = cs[cs.index >= "2010-01-01"]
    if len(cs) < 1250:
        continue
    trs = dd_events(cs)
    f250 = cs.shift(-250) / cs - 1.0
    for a, b, v, dd in trs:
        e = f250.asof(pd.Timestamp(a))
        if pd.notna(e):
            all_e.append(e)
    for i in range(500, len(cs)):
        t = None
    all_r.append((name, len(trs)))
tot = np.array(all_e)
print(f"\n30行业 回撤≤-50%锚: {len(tot)}个事件 250日前向 均值{np.nanmean(tot) * 100:+.1f}%/"
      f"中位{np.nanmedian(tot) * 100:+.1f}% 胜率{np.nanmean(tot > 0) * 100:.0f}%")
print("(对比: temp<12锚 695事件 250日前向 均值+21.6% 中位+18%)")
