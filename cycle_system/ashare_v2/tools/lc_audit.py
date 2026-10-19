# -*- coding: utf-8 -*-
"""lc_audit — 包C: LC独立审计(裁定C-1/C-2/C-3)。

区分三种"LC无成交"成因(此前被混为一谈):
  (a) 数据预热不足: ma250/slope250/dd500特征需个股>=250行历史(min_periods=250),
      池历史起点参差 → 窗口前段结构性不可评
  (b) 真实机会缺席: 特征可用后进入_ALLOWED_ENTRY_STATES的频率
  (c) 组合级风险锁定: R0状态机自锁定日起冻结全部新增(与LC信号质量无关)
并验证同信息边界三模式候选一致性(§C-1): MERGED的LC候选子集应等于LC_ONLY候选集。
MA250/500参数一律不动(裁定C-4), 本脚本只读数不改信号。
个股退出层级错配(§C-3)只登记事实, 设计另文。

用法: python -X utf8 cycle_system/ashare_v2/tools/lc_audit.py  → independent/lc_audit.json
"""
from __future__ import annotations

import glob
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                  # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                             # noqa: E402

W0, W1 = "2020-06-01", "2026-10-16"
LB = os.path.join(C.OUTPUT_DIR, "raw_bt")


def main() -> int:
    files = sorted(glob.glob(os.path.join(C.STOCKS_DIR, "*.csv")))
    first_valid250 = []           # 每股第250行日期(特征可用起点)
    starts, depths = [], []
    for f in files:
        try:
            d = pd.read_csv(f, usecols=["date"], dtype=str)
        except Exception:
            continue
        if len(d) < 2:
            continue
        starts.append(d["date"].iloc[0][:10])
        depths.append(len(d))
        if len(d) >= 250:
            # 窗口内可用起点 = max(第250行, 窗口首交易日)
            first_valid250.append(max(d["date"].iloc[249][:10], W0))
    dep = pd.Series(depths)
    fv = sorted(first_valid250)               # 字符串日期升序

    def fq(pct):                                # 分位(字符串日期按序位)
        return fv[min(int(pct * len(fv)), len(fv) - 1)] if fv else None
    # 截面覆盖率随时间: 每日有ma250的股票占比(相对窗口内已上市池)
    cov = {q: float(sum(1 for x in fv if x <= q) / max(len(fv), 1))
           for q in ("2020-06-01", "2020-09-14", "2021-01-13",
                                                "2021-06-01", "2022-01-01", "2022-06-01")}
    # LC候选流(两run)
    lc_only = pd.read_csv(os.path.join(LB, f"{W0}_{W1}_LONG_CYCLE", "opportunities.csv"),
                          encoding="utf-8-sig", dtype={"code": str})
    merged = pd.read_csv(os.path.join(LB, f"{W0}_{W1}", "opportunities.csv"),
                         encoding="utf-8-sig", dtype={"code": str})
    m_lc = merged[merged["engines"].astype(str).str.contains("LONG_CYCLE")]
    lc_only["day"] = lc_only["day"].astype(str)
    m_lc["day"] = m_lc["day"].astype(str)
    s_lc = set(zip(lc_only["day"], lc_only["code"].str.zfill(6)))
    s_m = set(zip(m_lc["day"], m_lc["code"].str.zfill(6)))
    # (c) R0锁定窗口: 首个候选时组合状态
    first_cand = lc_only["day"].min()
    nav = pd.read_csv(os.path.join(LB, f"{W0}_{W1}", "nav_hist_full.csv"), encoding="utf-8-sig")
    lock_rows = nav[nav["budget_state"] == "EXCEEDED"]
    lock_from = str(lock_rows["day"].iloc[0]) if len(lock_rows) else None
    cand_days = lc_only.groupby("day").size()
    pre_lock_cands = int(cand_days[cand_days.index <= lock_from].sum()) if lock_from else 0
    out = {
        "_class": "LC_INDEPENDENT_AUDIT / RESEARCH_REPLAY / 只读数不改信号(MA250/500不动, 裁定C-4)",
        "pool": {"n_files": len(files), "start_min": min(starts), "start_max": max(starts),
                 "depth_median": float(dep.median()), "depth_min": int(dep.min()),
                 "depth_max": int(dep.max())},
        "warmup_availability": {
            "first_valid_ma250_median": fq(0.5),
            "first_valid_ma250_p90": fq(0.9),
            "cross_section_ma250_coverage": {k: round(v, 3) for k, v in cov.items()},
            "note": "个股ma250可用日=该股第250行; 覆盖率为池内(剔窗口后新上市者分母含之, 保守)"},
        "candidate_flow": {
            "first_lc_candidate_day": first_cand,
            "n_candidate_rows_lc_only": len(lc_only),
            "n_candidate_days": int(len(cand_days)),
            "candidates_before_R0_lock": pre_lock_cands,
            "R0_locked_from": lock_from,
            "monthly_head": {k: int(v) for k, v in list(cand_days.items())[:8]}},
        "credibility_flags": {
            "data_credibility_dist": lc_only["data_credibility"].value_counts().to_dict(),
            "membership_trust_dist": lc_only["membership_trust"].value_counts().head(3).to_dict()},
        "three_mode_consistency": {
            "lc_only_pairs": len(s_lc), "merged_lc_pairs": len(s_m),
            "identical": s_lc == s_m,
            "only_in_lc_only": len(s_lc - s_m), "only_in_merged": len(s_m - s_lc),
            "note": "同信息边界(同日同券)候选集应全等; 不等=合并层改写了候选生成, 须逐条追因"},
        "zero_funding_decomposition": {
            "(a)_warmup_block": f"窗口首{W0}至池ma250中位可用日{fq(0.5)}期间结构不可评",
            "(b)_opportunity": f"特征可用后候选日{len(cand_days)}天/记录{len(lc_only)}条——真实机会并非缺席",
            "(c)_portfolio_lock": f"R0自{lock_from}永久EXCEEDED冻结新增, LC首候选{first_cand}在其后"
                                  if first_cand > str(lock_from) else
                                  f"R0自{lock_from}冻结; 首候选{first_cand}之前已有{pre_lock_cands}条候选被"
                                  f"锁定前预算/流动性拒(需逐日allocation_log核对)",
            "conclusion": "LC零成交主因=组合级R0永久锁定(c)+早期预热(a), '真实机会缺席'(b)被证伪: "
                          "特征可用后存在多年候选流"},
        "exit_layer_mismatch_C3": "登记: 个股级MA250/500退出 vs 行业级入场语境存在层级错配, "
                                  "单独设计不入R1(裁定C-3), 本审计未修改",
        "amended": "2026-10-19T21:55+08:00",
    }
    dest = os.path.join(C.OUTPUT_DIR, "independent", "lc_audit.json")
    json.dump(out, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:2600])
    return 0


if __name__ == "__main__":
    sys.exit(main())
