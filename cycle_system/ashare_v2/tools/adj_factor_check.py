# -*- coding: utf-8 -*-
"""adj_factor_check — 阶段1-3: 复权因子自洽与除权日收益连续性验证(tools域, 只读)。

目的(RAW重跑前置): 证明 raw 域逐日估值在除权除息日会产生收益断裂, 而按
factor_t/factor_{t-1} 对持仓股数做换算后可与 adj 域收益对齐——给明晨
runner RAW 估值提供正确换算式, 并审计面板 factor 本身的自洽性。

校验: A) close_adj/close 与 adj_factor 列一致; B) 除权日集合(factor变化)与其
raw/adj 日收益差 == factor 比率; C) 按股数换算后的模拟 RAW 持仓收益 vs adj 收益差
分布(应≈0, 残差=现金分红税等近似, 显式声明)。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                              # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                   # noqa: E402


def main() -> int:
    panel = DataPortal(data_mode=C.PIT_RESEARCH).price_panel()
    g = panel.sort_values(["code", "date"]).groupby("code", group_keys=False)
    f = g["adj_factor"].apply(lambda s: s / s.shift(1))
    r_raw = g["close"].apply(lambda s: s / s.shift(1))
    r_adj = g["close_adj"].apply(lambda s: s / s.shift(1))
    implied = panel["close_adj"] / panel["close"]
    a_err = float((implied / panel["adj_factor"]).dropna().sub(1).abs().max())
    ex_days = f.notna() & (f.sub(1).abs() > 1e-9)
    big_jump = f.notna() & (f.sub(1).abs() > 0.005)          # 真除权事件量级(>0.5%), 区别于hfq基准日漂移
    resid = (r_adj - r_raw * f)[ex_days].abs()
    normal = (r_adj - r_raw * f)[f.notna() & ~ex_days].abs()
    n_codes = int(panel["code"].nunique())
    ann_drift = float(np.nanmedian(f.dropna()) - 1)
    out = {
        "A_factor_identity(close_adj/close==adj_factor)": {"max_rel_err": round(a_err, 10),
                                                           "ok": bool(a_err < 1e-6)},
        "B_ex_day_universe": {"codes": n_codes,
                              "factor_drift_rows": int(ex_days.sum()),
                              "drift_share_pct": round(float(ex_days.sum()) / max(1, f.notna().sum()), 4),
                              "true_ex_event_rows(|df|>0.5%)": int(big_jump.sum()),
                              "median_daily_drift": round(ann_drift, 6),
                              "largest_single_jump": round(float(f[big_jump].max()), 4) if big_jump.any() else None,
                              "smallest_single_jump": round(float(f[big_jump].min()), 4) if big_jump.any() else None,
                              "finding": "hfq因子为滚动基准量: 逐日微漂(95.6%行)非除权事件; 真除权(|df|>0.5%)仅"
                                         f"{int(big_jump.sum())}行。逐日按factor换算股数会把漂移放大成持仓失真"},
        "C_return_continuity": {
            "resid_on_ex_days_median": round(float(resid.median()), 6),
            "resid_on_ex_days_p99": round(float(resid.quantile(0.99)), 6),
            "resid_normal_days_median": round(float(normal.median()), 6),
            "ok_alignment": bool(resid.median() < 1e-6 and normal.median() < 1e-9),
            "note": "resid=close_adj收益 - raw收益×factor比率; 若≈0则factor完全解释除权断裂; "
                    "若个别>1e-4提示现金分红未复入(RAW估值需股数换算而非价格换算)"},
        "D_implication_for_raw_valuation": "RAW账本定稿: 成交/股数/费用/NAV全raw且**股数不因因子换算**(漂移会失真); "
                                           "除权现金分红在raw域无现金入账→raw NAV结构性低约(年分红率), 故双轨呈报: "
                                           "raw NAV(主账本)+adj NAV(对照) + 两者差额按真除权日(|df|>0.5%)列表分解",
    }
    op = os.path.join(C.OUTPUT_DIR, "independent", "adj_factor_continuity.json")
    with open(op, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["A_factor_identity(close_adj/close==adj_factor)"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
