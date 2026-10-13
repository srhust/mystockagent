# -*- coding: utf-8 -*-
"""审查十一轮: MU底部Nowcaster历史盲回放。
九轮逐轮归因(首次预警提前/滞后量) + 全期指标(FA/年, 预警后继续下跌MAE, Brier, 校准) + 失败预警全记录。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from engine import metrics
from bottom_nowcaster import build_labels, build_features, fit_expanding

mu = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "MU.csv"), parse_dates=["date"]).sort_values("date").set_index("date")
c = mu["close"].astype(float)
cal = c.index
cycles = pd.read_csv(os.path.join(config.DATA_DIR, "mu", "cycles.csv"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)), ))) if False else None

# 重建zigzag谷底位置(与mu_cycle_audit一致: 30%反转)
lo, hi, trend, pivots = c.iloc[0], c.iloc[0], 0, []
for i in range(1, len(c)):
    x = c.iloc[i]
    if trend >= 0:
        if x > hi:
            hi, lo = x, x
        elif x <= hi * 0.70:
            pivots.append(("H", cal[i - 1] if False else cal[i]))  # 简化: 峰在破位点附近
            trend, lo = -1, x
    if trend <= 0:
        if x < lo:
            lo = x
        elif x >= lo * 1.30:
            pivots.append(("L", cal[i]))
            trend, hi = 1, x
trough_dates = [d for k, d in pivots if k == "L"]
trough_idx = [cal.get_indexer([pd.Timestamp(d)])[0] for d in trough_dates]

sox_fp = os.path.join(config.DATA_DIR, "mu", "SOX.csv")
sox = pd.read_csv(sox_fp, parse_dates=["date"]).set_index("date")["close"].astype(float) if os.path.exists(sox_fp) else None
feats = build_features(c, trough_idx, sox)
labels = build_labels(c, trough_idx)
preds, coefs = fit_expanding(feats, labels)

print("=== 特征权重(最后一次训练) ===")
for t, cf in coefs.items():
    print(t, cf)

for thr in (0.5,):
    print(f"\n=== 阈值{thr}: 九轮逐轮首次预警归因 ===")
    p = preds["P_bottom_20d"]
    warn_days = [i for i in range(len(p)) if p.iloc[i] >= thr]
    # 合并连续预警段
    segs = []
    for i in warn_days:
        if segs and i - segs[-1][-1] <= 5:
            segs[-1].append(i)
        else:
            segs.append([i])
    for _, row in cycles.iterrows():
        t0 = cal.get_indexer([pd.Timestamp(row.start)])[0]
        cand = [s[0] for s in segs if t0 - 250 <= s[0] <= t0 + 60]
        if not cand:
            print(f"{row.cycle_id} ({row.type}): 无预警(漏检)")
            continue
        s0 = cand[-1]  # 距谷底最近的预警段起点
        lead = t0 - s0
        mae = float(c.iloc[s0:min(s0 + 120, len(c))].min() / c.iloc[s0] - 1)
        print(f"{row.cycle_id} ({row.type}): 首警{s0 - t0:+d}日(负=提前) 警后MAE{mae*100:+.0f}% 概率{p.iloc[s0]:.2f}")
    # 假警报
    zone = set()
    for t in trough_idx:
        zone.update(range(max(0, t - 10), min(len(c), t + 11)))
    fa = [s for s in segs if not any(k in zone for k in s)]
    yrs = (cal[-1] - cal[0]).days / 365.25
    print(f"\n预警段{len(segs)}次, 假警报{len(fa)}次 = {len(fa)/yrs:.1f}/年")
    for s in fa[:12]:
        i0 = s[0]
        mae = float(c.iloc[i0:min(i0 + 120, len(c))].min() / c.iloc[i0] - 1)
        print(f"  假警 {cal[i0].date()} P={p.iloc[i0]:.2f} 警后120日MAE{mae*100:+.0f}%")
    # Brier + 校准
    print("\n=== Brier/校准(扩展窗预测 vs 事后标签) ===")
    for t in ("P_bottom_20d", "P_reversal_60d", "P_upcycle_120d"):
        pr, y = preds[t], labels[t].values
        m = np.isfinite(pr.values) & np.isfinite(y)
        brier = float(np.mean((pr.values[m] - y[m]) ** 2))
        print(f"{t}: Brier {brier:.3f} n={m.sum()}")
        for lo_b in (0.4, 0.6, 0.8):
            mm = m & (pr.values >= lo_b) & (pr.values < lo_b + 0.2)
            if mm.sum() >= 10:
                print(f"   P∈[{lo_b:.1f},{lo_b+0.2:.1f}): 实际发生率{y[mm].mean():.2f} (n={mm.sum()})")
