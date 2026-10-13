# -*- coding: utf-8 -*-
"""阶段(stage)与温度的关系 + 分阶段入场前向收益对比。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs = obj["dfs"]
sectors = sorted(dfs)
cal = dfs[sectors[0]].index
for s in sectors:
    cal = cal.union(dfs[s].index)
cdf = pd.DataFrame({s: dfs[s]["close"] for s in sectors}).reindex(cal)
tdf = pd.DataFrame({s: dfs[s]["temp"] for s in sectors}).reindex(cal)
sdf = pd.DataFrame({s: dfs[s]["stage"].astype(str) for s in sectors}).reindex(cal)
f250 = cdf.pct_change(250).shift(-250)
f120 = cdf.pct_change(120).shift(-120)

# 1) 阶段×温度分布(结构关系)
print("== 阶段 x 温度区间 占比(全样本全部板块日) ==")
tb = pd.cut(tdf.stack(), [0, 12, 20, 40, 60, 75, 100],
            labels=["<12", "12-20", "20-40", "40-60", "60-75", ">75"])
st = sdf.stack()
ct = pd.crosstab(st, tb, normalize="columns") * 100
print(ct.round(1).to_string())

# 2) 深寒区(temp<25)内分阶段入场的前向收益
print("\n== temp<25 内按阶段分组: 前向收益 ==")
mask = (tdf < 25) & f250.notna() & f120.notna()
for stg in ["bottom", "recovery", "warmup", "decline"]:
    m = mask & (sdf == stg)
    v250, v120 = f250[m], f120[m]
    if m.sum().sum() < 30:
        continue
    print(f"  {stg:9s}: 250日 {np.nanmean(v250.values) * 100:+.1f}%/中位{np.nanmedian(v250.values) * 100:+.1f}%"
          f"/胜率{(v250.values > 0).mean() * 100:.0f}% | 120日 {np.nanmean(v120.values) * 100:+.1f}% "
          f"(n={int(m.sum().sum())})")
m_all = mask
print(f"  {'全部':9s}: 250日 {np.nanmean(f250[m_all].values) * 100:+.1f}%/中位"
      f"{np.nanmedian(f250[m_all].values) * 100:+.1f}% (n={int(m_all.sum().sum())})")

# 3) 温度二分(<12 vs 12-25)同阶段对比
for stg in ["bottom", "recovery"]:
    mA = (tdf < 12) & (sdf == stg) & f250.notna()
    mB = (tdf >= 12) & (tdf < 25) & (sdf == stg) & f250.notna()
    if mA.sum().sum() > 30 and mB.sum().sum() > 30:
        print(f"  {stg}: temp<12 {np.nanmean(f250[mA].values) * 100:+.1f}% (n={int(mA.sum().sum())}) "
              f"vs temp12-25 {np.nanmean(f250[mB].values) * 100:+.1f}% (n={int(mB.sum().sum())})")
