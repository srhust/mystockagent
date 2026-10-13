# -*- coding: utf-8 -*-
"""cycle_labels.py (P0, CycleStart-v1第一步): 双标签 + 漏检诊断。
标签1 历史谷底(冻结): dd谷底<=-30%, 修复段分隔, 两谷底间需dd>=-15%。
标签2 有效启动(路径依赖): 信号日t后120日内 先涨+15% 先于 先跌-10% = 成功; 先跌-10%先=失败; 均未触发=超时。
      阈值按行业波动率缩放: 目标=g×s, 风险=-r×s, s=clip(0.20/vol250, 0.5, 2.0)。
诊断: 每个谷底是否被基线信号(temp<12 ∨ 自适应回撤)±60日覆盖; 未覆盖者按深度/形态分类。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config

HORIZON, G, R = 120, 0.15, 0.10


def effective_start_labels(close, vol_win=250):
    """v2: 委托 reversal_detector.effective_start (真first-touch triple-barrier, 年化波动障碍)。"""
    from reversal_detector import effective_start
    return effective_start(close, vol_win=vol_win)


def troughs(close, *, depth=0.30, recover=0.15):
    """冻结谷底定义: dd<=-depth 谷底, 期间需出现 dd>=-recover 修复段。"""
    dd = (close / close.cummax() - 1.0).values
    n = len(dd)
    mins = [i for i in range(1, n - 1)
            if np.isfinite(dd[i]) and dd[i] <= -depth and dd[i] <= dd[i - 1] and dd[i] <= dd[i + 1]]
    cyc = []
    for i in mins:
        if cyc:
            seg = dd[cyc[-1]:i]
            if not (np.isfinite(seg) & (seg >= -recover)).any():
                if dd[i] < dd[cyc[-1]]:
                    cyc[-1] = i
                continue
        cyc.append(i)
    return cyc, dd


def baseline_events(close, *, temp_th=12.0, floor=0.30, k=0.70, win=1250):
    lo, hi = close.rolling(win, min_periods=500).min(), close.rolling(win, min_periods=500).max()
    pp = (close - lo) / (hi - lo)
    r120 = close.pct_change(120).rolling(win, min_periods=500).rank(pct=True)
    r60 = close.pct_change(60).rolling(win, min_periods=500).rank(pct=True)
    temp = np.asarray(100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)).ravel()
    dd = (close / close.cummax() - 1.0).values
    dep = pd.Series(-dd).rolling(win, min_periods=250).max().values
    thr = np.maximum(floor, k * dep)
    return [i for i in range(len(dd)) if np.isfinite(dd[i]) and ((np.isfinite(temp[i]) and temp[i] < temp_th) or dd[i] <= -thr[i])]


def diagnose_sector(name, close, out):
    cyc, dd = troughs(close)
    ev = baseline_events(close)
    lab = effective_start_labels(close)
    eff_at_trough = [1 if (np.isfinite(lab.iloc[t]) and lab.iloc[t] == 1) else 0 for t in cyc]
    for t0 in cyc:
        covered = any(abs(s - t0) <= 60 for s in ev)
        depth = dd[t0]
        if covered:
            typ = "covered"
        elif depth > -0.40:
            typ = "浅周期(-30%~-40%)"
        else:
            typ = "深周期未触发(阈值错配)"
        out.append(dict(sector=name, trough_idx=t0, date=close.index[t0].date().isoformat(),
                        depth=round(float(depth), 3), covered=int(covered), type=typ,
                        effective_start=eff_at_trough[len(out) - sum(1 for _ in [])] if False else None))


def main():
    obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
    dfs = obj["dfs"]
    rows = []
    for name, d in dfs.items():
        c = d["close"].dropna()
        c.index = pd.to_datetime(c.index)
        c = c[c.index >= "2010-01-01"]
        if len(c) < 1250:
            continue
        cyc, dd = troughs(c)
        ev = baseline_events(c)
        lab = effective_start_labels(c)
        for t0 in cyc:
            covered = any(abs(s - t0) <= 60 for s in ev)
            depth = float(dd[t0])
            if covered:
                typ = "covered"
            elif depth > -0.40:
                typ = "浅周期(-30%~-40%)"
            else:
                typ = "深周期未触发"
            labv = lab.iloc[t0] if t0 < len(lab) else np.nan
            # 谷底后120日最大修复(谷底本身是局部低点, 路径标签无意义, 用修复幅度判定机会真实性)
            w = c.values[t0 + 1:t0 + 1 + HORIZON]
            fmax = float(w.max() / c.values[t0] - 1) if len(w) else np.nan
            effv = 1 if (np.isfinite(fmax) and fmax >= 0.30) else (0 if np.isfinite(fmax) else None)
            rows.append(dict(sector=name, date=str(c.index[t0].date()), depth=round(depth, 2),
                             covered=int(covered), type=typ,
                             eff_start=effv, recovery=round(fmax, 3) if np.isfinite(fmax) else None))
    R = pd.DataFrame(rows)
    n_all = len(R)
    n_cov = int(R.covered.sum())
    print(f"冻结谷底: {n_all}轮 | 基线覆盖(±60日): {n_cov} ({n_cov/n_all*100:.0f}%) | 漏检: {n_all-n_cov}")
    miss = R[R.covered == 0]
    print("\n漏检分类:")
    print(miss.type.value_counts().to_string())
    print("\n全部谷底中'有效启动'占比:", f"{(R.eff_start == 1).sum()}/{R.eff_start.notna().sum()} = {(R.eff_start == 1).sum() / max(1, R.eff_start.notna().sum()) * 100:.0f}%")
    m_eff = miss[miss.eff_start == 1]
    print(f"漏检中属于有效启动的: {len(m_eff)}轮 (这些是基线错过的真实机会)")
    print(m_eff.groupby("type").size().to_string() if len(m_eff) else "")
    R.to_csv(os.path.join(config.DATA_DIR, "..", "output", "cycle_labels_diagnosis.csv"), index=False, encoding="utf-8-sig")
    print("\n明细已存 output/cycle_labels_diagnosis.csv")


if __name__ == "__main__":
    main()

