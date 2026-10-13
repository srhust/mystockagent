# -*- coding: utf-8 -*-
"""P-DIP 组合回撤买点测试: 组合回撤≤-20% 用现金缓冲放大暴露1.25倍(≤100%), 修复到-8%恢复。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

from backtest_swing import load_inputs, simulate  # noqa: E402
from reversal_study import build_rules  # noqa: E402

WINDOWS = [("2010-01-01", "2010-2026 全样本"), ("2017-01-01", "2017-2026 样本外")]
inp = load_inputs()
rules = build_rules(inp)
base_x4 = rules["loop"](rules["entry"]["E0"], rules["exit"]["X4"])

RUNS = [
    ("v1基线+PD", None, dict(sa=False, sb=False, sc=False, sd=False, sz=False, pd=True)),
    ("v2稳健(X4)+PD", base_x4, dict(sa=False, sb=False, sc=False, sd=False, sz=False, pd=True)),
    ("v3进取(X4+SZ)+PD", base_x4, dict(sa=False, sb=False, sc=False, sd=False, sz=True, pd=True)),
]
out = []
for wstart, wtag in WINDOWS:
    i0 = int(inp["cal"].searchsorted(pd.Timestamp(wstart)))
    saved = inp["arr"]["base"]
    for name, b_over, flags in RUNS:
        if b_over is not None:
            inp["arr"]["base"] = b_over
        r = simulate(inp, i0, flags, label=f"{wtag[:9]}|{name}")
        inp["arr"]["base"] = saved
        m = r["metrics"]
        line = (f"{wtag} | {name}: 年化{m['cagr'] * 100:+.1f}% MDD{m['max_drawdown'] * 100:.1f}% "
                f"Sharpe{m['sharpe']:.2f} 笔数{m.get('n_trades', 0)}")
        print(line, flush=True)
        out.append(line)
with open(r"E:\mystockagent\output\pdip_results.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
