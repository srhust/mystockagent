# -*- coding: utf-8 -*-
"""cycle_ranker.py (P1, CycleStart-v1 阶段二): 候选质量排序。
阶段一(规则)生成候选日 -> 本模块预测 P(有效启动|X_t)。
特征(t日可得): temp_v4/dd252/rs20/rs_long/广度变化/广度水平/量比/3年价格位置。
标签: effective_start(+15%×s先于-10%×s, 120日)。模型: LogisticRegression(简单基准)。
分组OOS: train<=2019, test>=2020; 同周期重叠样本用sector聚类说明(不做伪独立声称)。"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from reversal_detector import effective_start

FEATURES = ["temp", "dd252", "rs20", "rs_long", "bd20", "breadth", "vol_ratio", "pos3y"]


def build_dataset(dfs, ref):
    X, y, meta = [], [], []
    for name, d in dfs.items():
        c = d["close"].dropna()
        c.index = pd.to_datetime(c.index)
        c = c[c.index >= "2010-01-01"]
        if len(c) < 1250:
            continue
        dd252 = c / c.rolling(252, min_periods=120).max() - 1.0
        ma20 = c.rolling(20).mean()
        rs = c / ref.reindex(c.index)
        rs20 = rs.pct_change(20)
        rs_long = rs / rs.rolling(252, min_periods=120).mean() - 1.0
        br = d["breadth"].reindex(c.index) if "breadth" in d else None
        bd20 = (br - br.shift(20)) if br is not None else pd.Series(0.0, index=c.index)
        brlvl = br if br is not None else pd.Series(0.5, index=c.index)
        vr = (d["amount"].reindex(c.index) if "amount" in d else pd.Series(np.nan, index=c.index))
        vol_ratio = vr.rolling(20).mean() / vr.rolling(60).mean()
        pos3y = c.rolling(756, min_periods=300).rank(pct=True)
        lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
        pp = (c - lo) / (hi - lo)
        r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
        r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
        temp = 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)
        lab = effective_start(c)
        cand = (watch := ((dd252 <= -0.25) | ((dd252 > -0.25) & (dd252 <= -0.08) & (rs_long > -0.15)))) & (c > c.rolling(20).max().shift(1))
        F = pd.DataFrame({"temp": temp, "dd252": dd252, "rs20": rs20, "rs_long": rs_long,
                          "bd20": bd20, "breadth": brlvl, "vol_ratio": vol_ratio, "pos3y": pos3y})
        for t in np.where(cand.values)[0]:
            if not np.isfinite(lab.values[t]):
                continue
            row = F.iloc[t]
            if row.isna().any():
                continue
            X.append(row.values.astype(float))
            y.append(int(lab.values[t]))
            meta.append((name, str(c.index[t].date())))
    return np.array(X), np.array(y), meta


def fit_lr(X, y, meta, split="2020-01-01"):
    tr = [i for i, (_, dt) in enumerate(meta) if dt < split]
    te = [i for i, (_, dt) in enumerate(meta) if dt >= split]
    if len(tr) < 100 or len(te) < 50:
        return None
    mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
    m = LogisticRegression(max_iter=1000, C=0.5)
    m.fit((X[tr] - mu) / sd, y[tr])
    p_tr, p_te = m.predict_proba((X[tr] - mu) / sd)[:, 1], m.predict_proba((X[te] - mu) / sd)[:, 1]
    out = {"n_train": len(tr), "n_test": len(te),
           "base_train": float(np.mean(y[tr])), "base_test": float(np.mean(y[te])),
           "auc_train": roc_auc_score(y[tr], p_tr), "auc_test": roc_auc_score(y[te], p_te)}
    # 分位检验: 测试集按预测分高位30% vs 其余
    q = np.quantile(p_te, 0.7)
    hi_m, lo_m = p_te >= q, p_te < q
    out["prec_top30"] = float(np.mean(np.array(y[te])[hi_m]))
    out["prec_rest"] = float(np.mean(np.array(y[te])[lo_m]))
    out["model"], out["mu"], out["sd"] = m, mu, sd
    out["coef"] = dict(zip(FEATURES, m.coef_[0].round(3)))
    return out


def score_proba(model, mu, sd, X):
    return model.predict_proba((X - mu) / sd)[:, 1]
