# -*- coding: utf-8 -*-
"""v2 策略组合测试: X4 期货背离出场 + S-Z 深回撤承接 + S-C 回调加仓。"""
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
    ("v1 基线(X0出场)", None, dict(sa=False, sb=False, sc=False, sd=False, sz=False)),
    ("X4期货背离出场", base_x4, dict(sa=False, sb=False, sc=False, sd=False, sz=False)),
    ("v2=X4+S-Z+S-C", base_x4, dict(sa=False, sb=False, sc=True, sd=False, sz=True)),
    ("v2s=X4+S-Z(精简)", base_x4, dict(sa=False, sb=False, sc=False, sd=False, sz=True)),
]
out = []
for wstart, wtag in WINDOWS:
    i0 = int(inp["cal"].searchsorted(pd.Timestamp(wstart)))
    saved = inp["arr"]["base"]
    for name, b_override, flags in RUNS:
        if b_override is not None:
            inp["arr"]["base"] = b_override
        r = simulate(inp, i0, flags, label=f"{wtag[:9]}|{name}")
        inp["arr"]["base"] = saved
        m, s = r["metrics"], r["stats"]
        line = (f"{wtag} | {name}: 年化{m['cagr'] * 100:+.1f}% MDD{m['max_drawdown'] * 100:.1f}% "
                f"Sharpe{m['sharpe']:.2f} 笔数{m.get('n_trades', 0)} | "
                f"S-Z加仓{s['sz_add']}次/胜率{(s['sz_win'] / s['sz_add'] * 100 if s['sz_add'] else 0):.0f}%"
                f"/单次{s['sz_avg_pnl'] * 100 if s['sz_pnl'] else 0:+.1f}% "
                f"S-C加仓{s['sc_add']}次/单次{s['sc_avg_pnl'] * 100 if s['sc_pnl'] else 0:+.1f}%")
        print(line, flush=True)
        out.append(line)
with open(r"E:\mystockagent\output\v2_results.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
