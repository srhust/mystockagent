# -*- coding: utf-8 -*-
"""生成v6: v4 + 行业组哑特征(内存/设备/模拟/代工, 分层建模的轻量实现) + precision@k评估(每年top-2预警预算)."""
src = open(r"E:\mystockagent\scripts\nowcaster_v4.py", encoding="utf-8").read()
old_ind = '''    ind = ind[["rev_yoy", "rev_accel", "gm", "inv_rev"]].astype(float)'''
new_ind = '''    grp = {"MU": "mem", "STX": "mem", "WDC": "mem", "AMAT": "eqp", "LRCX": "eqp", "TXN": "ana", "TSM": "fnd", "QCOM": "fnd", "INTC": "fnd"}.get(name, "oth")
    for g in ("mem", "eqp", "ana", "fnd"):
        ind[f"g_{g}"] = 1.0 if grp == g else 0.0  # 审查十二轮v6: 行业组哑特征
    ind = ind[["rev_yoy", "rev_accel", "gm", "inv_rev", "g_mem", "g_eqp", "g_ana", "g_fnd"]].astype(float)'''
assert old_ind in src
src = src.replace(old_ind, new_ind)
old_eval = '''    yrs = (d["close"].index[-1] - pd.Timestamp("2017-06-01")).days / 365.25
    print(f"{n} P98(thr={thr:.2f}): 2017+召回{hits} 假警{len(fa)}({len(fa)/yrs:.1f}/年) 提前中位{int(np.median(leads)) if leads else 'NA'}日 漏检{miss}")'''
new_eval = '''    yrs = (d["close"].index[-1] - pd.Timestamp("2017-06-01")).days / 365.25
    print(f"{n} P98(thr={thr:.2f}): 2017+召回{hits} 假警{len(fa)}({len(fa)/yrs:.1f}/年) 提前中位{int(np.median(leads)) if leads else 'NA'}日 漏检{miss}")
    # 审查十二轮v6: 固定预警预算 precision@k (每年top-2段)
    seg_p = [(float(np.nanmax(p.iloc[s])), s) for s in segs]
    seg_p.sort(reverse=True)
    years_n = max(1, len(pv) // 244)
    budget = max(1, int(2 * years_n))
    top = [s for _, s in seg_p[:budget]]
    hits_k = sum(1 for t in d["troughs"] if d["close"].index[t] >= pd.Timestamp("2017-06-01")
                 and any(t - 250 <= s[0] <= t + 60 for s in top))
    tr17 = sum(1 for t in d["troughs"] if d["close"].index[t] >= pd.Timestamp("2017-06-01"))
    print(f"   P@{budget}段(每年2段): 召回{hits_k}/{tr17} 精度{hits_k}/{min(len(top), tr17) if False else hits_k}/{len(top)}段")'''
assert old_eval in src
src = src.replace(old_eval, new_eval)
open(r"E:\mystockagent\scripts\nowcaster_v6.py", "w", encoding="utf-8").write(src)
print("v6 written OK")
