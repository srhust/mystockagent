# -*- coding: utf-8 -*-
"""商品周期位置因子检验: 深寒入场时对应商品在自身5年区间位置 -> 前向收益分离度。"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401
import config  # noqa: F401

meta = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))["meta"]
SEC2FUT = {n: m.get("fut") for n, m in meta.items() if m.get("fut")}
ev = pd.read_csv(os.path.join(config.OUTPUT_DIR, "factor_events.csv"), parse_dates=["date"])
ev = ev[ev["sector"].isin(SEC2FUT)].copy()
print(f"[events] 有期货映射的深寒事件: {len(ev)} ({ev['sector'].nunique()}个行业)", flush=True)

poss, lows = [], []
for _, r in ev.iterrows():
    fp = os.path.join(config.DATA_DIR, f"fut_{SEC2FUT[r['sector']]}.csv")
    f = pd.read_csv(fp, parse_dates=["date"]).set_index("date")["close"].astype(float)
    f = f[f.index <= r["date"]]
    if len(f) < 500:
        poss.append(np.nan); lows.append(np.nan); continue
    w = f.iloc[-1250:]
    poss.append(float((f.iloc[-1] - w.min()) / (w.max() - w.min())))
    lows.append(float(f.iloc[-1] / w.min() - 1))
ev["fut_pos"] = poss          # 商品在自身5年区间的位置(0=商品大底, 1=商品高位)
ev["fut_above_low"] = lows    # 商品已高于自身低点多少(股价领先商品的幅度)
ev = ev.dropna(subset=["fut_pos"])
print(f"[valid] 可评估: {len(ev)}", flush=True)

print("\n== H1: 商品周期位置分组 -> 行业250日前向收益 ==")
for lo, hi, tag in [(0, 0.3, "商品低位<30%"), (0.3, 0.6, "商品中位30-60%"), (0.6, 1.01, "商品高位>60%")]:
    g = ev[(ev.fut_pos >= lo) & (ev.fut_pos < hi)]
    if len(g) < 15:
        continue
    print(f"  {tag}: {g['f250'].mean() * 100:+.1f}%/中位{g['f250'].median() * 100:+.1f}% (n={len(g)})")
med = ev["fut_pos"].median()
g1, g2 = ev[ev.fut_pos <= med], ev[ev.fut_pos > med]
print(f"  中位数二分: 商品位置低 {g1['f250'].mean() * 100:+.1f}% (n={len(g1)}) vs 高 {g2['f250'].mean() * 100:+.1f}% (n={len(g2)})")

print("\n== H2: 股价领先商品的幅度(信号日商品距自身5年低点) ==")
print(f"  分布: 中位{ev['fut_above_low'].median() * 100:+.1f}% 均值{ev['fut_above_low'].mean() * 100:+.1f}% "
      f"| 商品仍在低点5%内: {(ev.fut_above_low <= 0.05).mean() * 100:.0f}% "
      f"| 已反弹>10%: {(ev.fut_above_low > 0.10).mean() * 100:.0f}%")
ev.to_csv(os.path.join(config.OUTPUT_DIR, "commodity_anchor_events.csv"), index=False, encoding="utf-8-sig")
print("\n[done] output/commodity_anchor_events.csv")
