# -*- coding: utf-8 -*-
"""bottom_nowcaster.py (审查十一轮P0: 实时周期底部预测核心)。
三层概率目标(标签为事后定义, 仅用于评估; 预测时只见T日及以前信息):
  P_bottom_20d   未来20交易日内进入底部区域( zigzag谷底±10日区域与(t,t+20]相交 )
  P_reversal_60d 未来60日内先触及+30%再触及-10%(first-touch反转成功, 反之为0)
  P_upcycle_120d 未来120日内先触及+50%再触及-15%(first-touch上行周期, 对齐周期定义涨幅≥50%)
特征(全部T日可得): dd252/ret40/vol比/pos1250/下跌持续天数/距上轮谷底天数(过去谷底在T已知)。
模型: LogisticRegression 扩展窗逐年重训(≥750日); 训练行要求标签窗口完整落在训练区间内(purge)。
输出"尚不确定": 概率∈[0.35,0.55]视为uncertain。"""
import numpy as np
import pandas as pd


def first_touch(close, i, horizon, up, dn):
    """(i, i+h]内先触及+up%还是-dn%(以i收盘为基准)。1=反转成功, 0=失败, None=窗口不足/未触。"""
    base = close.iloc[i]
    cv = close.iloc[i + 1:i + 1 + horizon].values
    if len(cv) < horizon:
        return None  # 观察窗不完整: 不产生标签
    up_l, dn_l = base * (1 + up), base * (1 + dn)
    for x in cv:
        if x >= up_l:
            return 1
        if x <= dn_l:
            return 0
    return 0  # 60/120日内既未+up也未-dn: 视为未反转


def build_labels(close, trough_idx, bottom_halfwidth=10):
    n = len(close)
    lab = pd.DataFrame(index=close.index)
    bottom_zone = set()
    for t in trough_idx:
        for k in range(max(0, t - bottom_halfwidth), min(n, t + bottom_halfwidth + 1)):
            bottom_zone.add(k)
    b = np.zeros(n)
    for i in range(n):
        if any(k in bottom_zone for k in range(i + 1, min(n, i + 21))):
            b[i] = 1
    lab["P_bottom_20d"] = b
    rev, upc = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(n):
        rev[i] = first_touch(close, i, 60, 0.30, -0.10) if i + 60 < n else np.nan
        upc[i] = first_touch(close, i, 120, 0.50, -0.15) if i + 120 < n else np.nan
    lab["P_reversal_60d"] = rev
    lab["P_upcycle_120d"] = upc
    return lab


def build_features(close, prev_trough_idx, sox=None):
    """全部T日可得。prev_trough_idx: T日已知的过去谷底位置列表。"""
    c = close
    f = pd.DataFrame(index=c.index)
    hi252 = c.rolling(252, min_periods=200).max()
    f["dd252"] = c / hi252 - 1.0
    f["ret40"] = c.pct_change(40)
    vol20 = c.pct_change().rolling(20).std()
    vol252 = c.pct_change().rolling(252, min_periods=200).std()
    f["vol_ratio"] = vol20 / vol252
    lo1250 = c.rolling(1250, min_periods=500).min()
    hi1250 = c.rolling(1250, min_periods=500).max()
    f["pos1250"] = (c - lo1250) / (hi1250 - lo1250)
    # 下跌持续: 距252日高点的天数
    hi_day = hi252.index.to_series().groupby([c.index.to_series().dt.year]).transform("first")  # placeholder
    days_since_high = pd.Series(np.arange(len(c)), index=c.index) - pd.Series(np.arange(len(c)), index=c.index).where(c >= hi252, np.nan).ffill()
    f["decline_days"] = days_since_high
    # 距上一谷底天数(T日已知的历史信息)
    pti = sorted(prev_trough_idx)
    dist = np.full(len(c), np.nan)
    for i, t in enumerate(pti):
        pass
    j = 0
    cur = np.nan
    for i in range(len(c)):
        while j < len(pti) and pti[j] <= i:
            cur = i - pti[j]
            j += 1
        dist[i] = cur
    f["days_since_prev_trough"] = dist
    if sox is not None:
        rs = (c / sox.reindex(c.index))
        f["rs20"] = rs.pct_change(20)
    # 审查十二轮: 结构性NaN中性填充(避免早期行全灭导致训练集枯竭)
    f["decline_days"] = f["decline_days"].fillna(0.0)
    f["days_since_prev_trough"] = f["days_since_prev_trough"].fillna(999.0)
    return f


def fit_expanding(feats, labels, *, start=750, targets=("P_bottom_20d", "P_reversal_60d", "P_upcycle_120d")):
    """扩展窗: 每年首个交易日重训; 训练行要求120日标签窗完整(去掉训练段末尾120行)。"""
    from sklearn.linear_model import LogisticRegression
    idx = feats.index
    n = len(idx)
    years = idx.year.values
    preds = {t: pd.Series(np.nan, index=idx) for t in targets}
    coefs = {}
    for i in range(start, n):
        if i > start and years[i] == years[i - 1]:
            continue  # 每年重训一次
        tr_end = i - 120  # purge: 标签窗不跨入测试区
        X = feats.iloc[:tr_end]
        for t in targets:
            y = labels[t].iloc[:tr_end].values
            m = np.isfinite(y) & np.isfinite(X).all(axis=1).values
            if m.sum() < 200 or len(np.unique(y[m])) < 2:
                continue
            mu, sd = X[m].mean(), X[m].std().replace(0, 1)
            lr = LogisticRegression(C=0.5, max_iter=1000)
            lr.fit((X[m] - mu) / sd, y[m].astype(int))
            seg = feats.iloc[i:i + (n - i)]
            seg = feats.iloc[i:]
            Xz = (seg - mu) / sd
            preds[t].iloc[i:] = lr.predict_proba(Xz)[:, 1]
            coefs[t] = dict(zip(feats.columns, np.round(lr.coef_[0], 2)))
    return preds, coefs

