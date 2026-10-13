# -*- coding: utf-8 -*-
"""生成v9ml: v8 + 三项修复重评 — ①季度先算同比(as-of对齐) ②测试期P98→逐日expanding分位(as-of冻结) ③死特征days_since_prev_trough已复活(build_features修复自动生效)。匹配改严格一对一[w,w+120]+P@60/R@60/事件后续跌。"""
src = open(r"E:\mystockagent\scripts\nowcaster_v8.py", encoding="utf-8").read()
old_rev = """    ind["rev_yoy"] = ind["val"] / ind["val"].shift(4) - 1
    ind["rev_accel"] = ind["rev_yoy"] - ind["rev_yoy"].shift(1)"""
new_rev = """    _q = fund.sort_values("end").drop_duplicates("end", keep="last").reset_index(drop=True)  # 审查十四轮: 季度序列先算同比
    _q["rev_yoy_q"] = _q["val"] / _q["val"].shift(4) - 1
    _q["rev_accel_q"] = _q["rev_yoy_q"] - _q["rev_yoy_q"].shift(1)
    _q2 = _q.sort_values("filed").drop_duplicates("filed", keep="last").set_index("filed")[["rev_yoy_q", "rev_accel_q"]].reindex(c.index, method="ffill")
    ind["rev_yoy"] = _q2["rev_yoy_q"]
    ind["rev_accel"] = _q2["rev_accel_q"]"""
assert old_rev in src, "rev anchor"
src = src.replace(old_rev, new_rev)
old_thr = """    thr = float(np.quantile(pv, 0.98))"""
new_thr = """    thr_s = pv.expanding(min_periods=244).quantile(0.98).shift(1)  # 审查十四轮: as-of冻结阈值(每日用此前历史P98)"""
assert old_thr in src, "thr anchor"
src = src.replace(old_thr, new_thr)
old_seg = """        if np.isfinite(p.iloc[i]) and p.iloc[i] >= thr and dt >= pd.Timestamp("2017-06-01"):"""
new_seg = """        _thr = thr_s.get(dt, np.nan)
        if np.isfinite(p.iloc[i]) and np.isfinite(_thr) and p.iloc[i] >= _thr and dt >= pd.Timestamp("2017-06-01"):"""
assert old_seg in src, "seg anchor"
src = src.replace(old_seg, new_seg)
old_hit = """    for t in d["troughs"]:
        td = d["close"].index[t]
        if td < pd.Timestamp("2017-06-01"):
            continue
        cand = [s[0] for s in segs if t - 250 <= s[0] <= t + 60]
        if cand:
            hits += 1
            leads.append(cand[-1] - t)
        else:
            miss.append(str(td.date()))"""
new_hit = """    # 审查十四轮: 严格一对一匹配 [w, w+120], P@60=预警后60日内见谷占比, 事件后60日续跌
    used_t, p60, dd60 = set(), 0, []
    for w in [s[0] for s in segs]:
        ts = [t for t in d["troughs"] if w <= t <= w + 120 and t not in used_t and d["close"].index[t] >= pd.Timestamp("2017-06-01")]
        if ts:
            t0 = min(ts)
            used_t.add(t0)
            if t0 <= w + 60:
                p60 += 1
            dd60.append(float(d["close"].iloc[w:min(len(d["close"]), w + 60)].min() / d["close"].iloc[w] - 1))
    hits = len(used_t)
    n_ev = len(segs)
    rec60 = sum(1 for t in d["troughs"] if d["close"].index[t] >= pd.Timestamp("2017-06-01")
                and any(w <= t <= w + 60 for w in [s[0] for s in segs]))
    n_tr17 = sum(1 for t in d["troughs"] if d["close"].index[t] >= pd.Timestamp("2017-06-01"))"""
assert old_hit in src, "hit anchor"
src = src.replace(old_hit, new_hit)
old_pr = """    print(f"{n} P98(thr={thr:.2f}): 2017+召回{hits} 假警{len(fa)}({len(fa)/yrs:.1f}/年) 提前中位{int(np.median(leads)) if leads else 'NA'}日 漏检{miss}")"""
new_pr = """    print(f"{n}: 事件{n_ev} 严格命中{hits} P@60={p60}/{n_ev} R@60={rec60}/{n_tr17} 续跌中位{np.median(dd60):.1%}" if dd60 else f"{n}: 事件{n_ev} 无命中")"""
assert old_pr in src, "pr anchor"
src = src.replace(old_pr, new_pr)
open(r"E:\mystockagent\scripts\nowcaster_v9ml.py", "w", encoding="utf-8").write(src)
print("v9ml written OK")

