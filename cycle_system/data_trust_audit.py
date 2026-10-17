# -*- coding: utf-8 -*-
"""R31: 数据可信度审计——①manifest(机器可校验: 249CSV sha256/池/代码哈希/参数/GitSHA)
②历史时点股票池曲线(pool_at桶分布) ③退市清单 ④ST/停牌/数据缺失覆盖报告 ⑤行业归属穿越局限声明。
产出: output/ashare/data_trust_report.json + manifest.json"""
import hashlib
import json
import os
import subprocess
import sys

import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m
import ashare_stock_pipeline as sp

OUT = r"E:\mystockagent\output\ashare"


def sha(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


# ── 1) manifest ──
csvs = sorted([os.path.join(r"E:\mystockagent\data\stocks", f) for f in os.listdir(r"E:\mystockagent\data\stocks") if f.endswith(".csv")])
git_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=r"E:\mystockagent", capture_output=True, text=True).stdout.strip()
manifest = {
    "generated": str(pd.Timestamp.now()),
    "git_sha": git_sha,
    "n_csv": len(csvs),
    "csv_hashes": {os.path.basename(p): sha(p) for p in csvs},
    "code_hashes": {f: sha(os.path.join(r"E:\mystockagent\cycle_system", f)) for f in ("swing_m1.py", "ashare_executor.py", "ashare_tradeable_pool.py", "ashare_stock_pipeline.py")},
    "candidates": {
        "B0_equal": {"strategy": "expt", "gate": True, "exit_mode": "monthly", "entry_mode": "daily", "cap": 0, "alloc_policy": "equal"},
        "B1_score": {"strategy": "expt", "gate": True, "exit_mode": "monthly", "entry_mode": "daily", "cap": 0, "alloc_policy": "score"},
        "F2_gate_realloc": {"strategy": "expt", "gate": True, "exit_mode": "monthly", "entry_mode": "daily", "cap": 0, "alloc_policy": "score", "risk_mode": "gate", "gate_realloc": "realloc", "s2_bad_win": 20, "s2_rs_th": 0.0},
    },
    "factor_caliber": "v2(rolling20: mom20/stab20/liq20, 每日截面z, score_3f)——与旧60日尾部窗口近似非恒等",
    "status": "candidates frozen; F2 not default; retrospective WF only",
}
json.dump(manifest, open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# ── 2) 历史时点股票池曲线 ──
panel = m.load_panel()
pool_pts = {}
for d in ("2018-06-29", "2020-01-02", "2021-09-22", "2023-06-30", "2025-12-31"):
    try:
        b = m.pool_at(d)["buckets"]
        pool_pts[d] = {k: len(v) for k, v in b.items()}
    except RuntimeError:
        pool_pts[d] = "calendar-unavailable"

# ── 3) 退市确认清单 ──
delisted = list(getattr(sp, "DELISTED_CONFIRMED", {}).keys())

# ── 4) 数据缺失覆盖(每股缺交易日数) ──
dates_all = pd.DatetimeIndex(sorted(panel["date"].unique()))
full = dates_all
gaps = panel.groupby("code")["date"].nunique()
gap_ser = (len(full) - gaps).sort_values(ascending=False)
coverage = {"n_codes": int(len(gaps)), "perfect_coverage_codes": int((gap_ser == 0).sum()), "worst10_gap_tdays": {c: int(v) for c, v in gap_ser.head(10).items()}}

# ── 5) 行业归属与ST局限声明 ──
sector_null = int(panel.drop_duplicates("code")["sector"].isna().sum())
report = {
    "generated": str(pd.Timestamp.now()),
    "git_sha": git_sha,
    "pool_points": pool_pts,
    "delisted_confirmed_n": len(delisted),
    "delisted_confirmed_sample": delisted[:15],
    "coverage": coverage,
    "survivorship": {
        "known_limitation": "当前池=configured_stocks当前快照(249只); 历史时点池经pool_at排除未上市/退市确认/历史不足, 但'2020年后才入选当前池的成长股'在早期时点不存在——回测起点(2020-04)的池=当时已上市且现在仍被跟踪的股票, 存在幸存者偏差(入选基于当前名单)",
        "delisted_in_pool_n": len(delisted),
    },
    "industry_lookahead": {
        "known_limitation": "行业归属使用当前快照分类(无历史时点映射数据源); T日使用当前行业——重组/更名股票的行业穿越风险存在, 无法自动检出",
        "sector_null_n": sector_null,
    },
    "st_suspend": {
        "known_limitation": "历史ST/停牌状态无独立数据源; 依赖: ①数据缺失=不可交易(保守) ②涨跌停拒单已测 ③DATA_UNVERIFIED桶(因子覆盖率<99.9%不进TRADEABLE)",
        "rejection_reasons_current": json.load(open(os.path.join(OUT, "swing_m1_F2_realloc_full.json"), encoding="utf-8")).get("reject_detail", {}),
    },
    "oos_status": "retrospective walk-forward only(2020-2026已反复研究); 严格OOS需独立历史样本或冻结日后新数据",
}
json.dump(report, open(os.path.join(OUT, "data_trust_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(json.dumps({"pool_points": pool_pts, "delisted_n": len(delisted), "coverage": coverage}, ensure_ascii=False, indent=1))
print("TRUST-OK")
