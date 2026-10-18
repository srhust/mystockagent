# -*- coding: utf-8 -*-
"""R32b: AKShare申万POC(只读, 免费, 不改回测)——30样本x4时点历史行业归属可得性验证.
输出: output/ashare/industry_pit_poc.json (每条: code/查询日/行业码/分类版本/来源/有效起止/验证状态) + 四指标."""
import json
import sys

import akshare as ak
import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

panel = m.load_panel()
sector_of = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()

# 1) 申万二级+三级清单→目标行业
info2, info3 = ak.sw_index_second_info(), ak.sw_index_third_info()
name2, code2 = [c for c in info2.columns if "名称" in c][0], [c for c in info2.columns if "代码" in c][0]
name3, code3 = [c for c in info3.columns if "名称" in c][0], [c for c in info3.columns if "代码" in c][0]
targets = {}
for want in ("农化制品", "证券", "房地产开发", "工业金属", "煤炭开采", "白酒"):
    for inf, nc, cc, lvl in ((info2, name2, code2, "L2"), (info3, name3, code3, "L3")):
        for _, rr in inf[inf[nc].str.contains(want, na=False)].iterrows():
            targets[rr[cc]] = {"name": rr[nc], "lvl": lvl}
print("target_industries:", {k: v["name"] for k, v in targets.items()})

# 2) 抓成分(含纳入时间)
cons = {}
for code, meta in targets.items():
    try:
        df = ak.sw_index_second_cons(symbol=code) if meta["lvl"] == "L2" else ak.sw_index_third_cons(symbol=code)
        if len(df):
            cons[code] = {"name": meta["name"], "df": df}
    except Exception as ex:
        print("ERR", code, type(ex).__name__)
print("cons_n:", {k: len(v["df"]) for k, v in cons.items()})

# 3) 30样本(池内股, 覆盖目标行业+其他)
pool = sorted(sector_of.keys())
picks, seen = [], set()
for code, v in cons.items():
    for _, rr in v["df"].iterrows():
        sc = str(rr["股票代码"]).zfill(6)
        if sc in sector_of and sc not in seen and len(picks) < 24:
            picks.append((sc, code, str(rr.get("纳入时间", ""))))
            seen.add(sc)
for sc in pool:  # 补足30(非申万目标行业股→预期UNKNOWN)
    if sc not in seen and len(picks) < 30:
        picks.append((sc, "", ""))
        seen.add(sc)

# 4) 四时点判定
points = ["2020-06-30", "2021-09-22", "2023-06-30", "2025-12-31"]
recs, stats = [], {"n_query": 0, "verified": 0, "partial": 0, "unknown": 0}
for sc, ind_code, in_date in picks:
    ind = cons.get(ind_code, {}).get("name", "")
    ver = "SW2021(当前快照+纳入时间)"
    try:
        din = pd.Timestamp(in_date) if in_date else None
    except Exception:
        din = None
    for t in points:
        stats["n_query"] += 1
        T = pd.Timestamp(t)
        if din is not None and din <= T:
            stt = "VERIFIED"  # 纳入时间早于T且当前仍在成分——归属可知(仅当前成分, 无out_date)
            stats["verified"] += 1
        elif din is not None:
            stt = "PARTIAL"  # T时不在当前行业(纳入晚于T)——但历史所属行业不可见(无剔除记录)
            stats["partial"] += 1
        else:
            stt = "UNKNOWN"
            stats["unknown"] += 1
        recs.append({"code": sc, "query_date": t, "industry_code": ind_code, "industry": ind, "taxonomy": ver, "source": "AKShare sw_index_third_cons", "in_date": in_date, "out_date": None, "status": stt})

# 5) 变更检出率(纳入时间在2020-2026回测期内=期间调整过)
in_d = [pd.Timestamp(str(rr.get("纳入时间", ""))) for v in cons.values() for _, rr in v["df"].iterrows() if str(rr.get("纳入时间", "")) not in ("", "NaT", "nan", "None")]
chg = sum(1 for x in in_d if pd.Timestamp("2020-01-01") <= x <= pd.Timestamp("2026-10-01"))
out = {
    "sample_n": len(picks), "points": points, "records": recs,
    "metrics": {"query_success_rate": round(stats["verified"] / max(1, stats["n_query"]), 3), "verified_n": stats["verified"], "partial_n": stats["partial"], "unknown_n": stats["unknown"], "industry_change_detected_in_backtest_window": chg, "taxonomy_ambiguity": "申万2021版分类+当前成分纳入时间; 无out_date→被剔除历史成员不可见; in_date≤T不能证明分类名称当时已存在(版本歧义待查)"},
    "conclusion": "AKShare不满足PIT重建: ①无sw_index_second_cons(农化/工业金属/房地产开发等二级行业成分不可得, 801125等AttributeError) ②三级成分'纳入时间'列实际为空(851931证券50只成分dropna后0值)→历史时点归属不可重建; 被拦截股票的历史行业保持UNKNOWN",
    "limitation": "只读POC, 不修改回测行业映射, 不重跑策略; 下一步=评估Tushare index_member_all(需2000积分凭据, 经本地环境变量配置)或申万官方历史成分文件",
}
json.dump(out, open(r"E:\mystockagent\output\ashare\industry_pit_poc.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(json.dumps(out["metrics"], ensure_ascii=False))
print("POC-OK")
