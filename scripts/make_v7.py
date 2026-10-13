# -*- coding: utf-8 -*-
"""生成v7: 组内留一自交叉(LOOX)建模 — 每股模型只用同组成员训练(排除自身), 兼做③MU纯样本外与①完整分层。"""
src = open(r"E:\mystockagent\scripts\nowcaster_v6.py", encoding="utf-8").read()
old_ps = """        for n in meta:
            f, lab = train[n]
            fz = (f - mu_) / sd_"""
new_ps = """        for n in meta:
            f, lab = train[n]
            # 审查十二轮v7: 组内LOOX — 每股用同组成员训练并排除自身(横截面无自泄漏), 单例组保留自身
            GROUPS = {"MEM": ["MU", "STX", "WDC"], "EQP": ["AMAT", "LRCX"], "FND": ["QCOM", "INTC"], "ANA": ["TXN"]}
            pool = [x for x in next(g for g in GROUPS.values() if n in g) if x != n] or [n]
            Xg = pd.concat([train[x][0] for x in pool])
            yg = pd.concat([train[x][1][t] for x in pool])
            Xgn = Xg.replace(999.0, np.nan)
            Xgn = Xgn.dropna(axis=1, how="all")
            Xgn = Xgn.fillna(Xgn.median())
            okg = np.isfinite(Xgn).all(axis=1).values & np.isfinite(yg).values
            Xg2, yg2 = Xgn[okg], yg[okg]
            if len(Xg2) < 800 or yg2.nunique() < 2:
                continue
            mu_, sd_ = Xg2.mean(), Xg2.std().replace(0, 1)
            lrg = LogisticRegression(C=0.5, max_iter=1000).fit((Xg2 - mu_) / sd_, yg2)
            fz = (f - mu_) / sd_"""
assert old_ps in src, "ps anchor"
src = src.replace(old_ps, new_ps)
src = src.replace("fz = fz.reindex(columns=X2.columns).replace(999.0, np.nan).fillna(X2.median())",
                  "fz = fz.reindex(columns=Xg2.columns).replace(999.0, np.nan).fillna(Xg2.median())")
src = src.replace("p_in = lr.predict_proba(fz.iloc[half:])[:, 1]", "p_in = lrg.predict_proba(fz.iloc[half:])[:, 1]")
src = src.replace("segn = seg.reindex(columns=X2.columns).replace(999.0, np.nan).fillna(X2.median())",
                  "segn = seg.reindex(columns=Xg2.columns).replace(999.0, np.nan).fillna(Xg2.median())")
src = src.replace("preds[n][t].loc[seg.index] = iso.predict(lr.predict_proba((seg - mu_) / sd_)[:, 1])",
                  "preds[n][t].loc[seg.index] = iso.predict(lrg.predict_proba((seg - mu_) / sd_)[:, 1])")
assert "lrg.predict_proba((seg" in src
open(r"E:\mystockagent\scripts\nowcaster_v7.py", "w", encoding="utf-8").write(src)
print("v7 written OK")

