# -*- coding: utf-8 -*-
"""S-Z 深回撤承接(-20% from 持有期高点, 阶段门控) vs 基线 vs S-C 对照。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

from backtest_swing import load_inputs, simulate  # noqa: E402

WINDOWS = [("2010-01-01", "2010-2026 全样本"), ("2017-01-01", "2017-2026 样本外")]
RUNS = [
    ("基线", dict(sa=False, sb=False, sc=False, sd=False, sz=False)),
    ("S-Z 深回撤承接", dict(sa=False, sb=False, sc=False, sd=False, sz=True)),
    ("S-Z+S-C", dict(sa=False, sb=False, sc=True, sd=False, sz=True)),
]
out = []
inp = load_inputs()
for wstart, wtag in WINDOWS:
    i0 = int(inp["cal"].searchsorted(pd.Timestamp(wstart)))
    for name, flags in RUNS:
        r = simulate(inp, i0, flags, label=f"{wtag[:9]}|{name}")
        m, s = r["metrics"], r["stats"]
        line = (f"{wtag} | {name}: 年化{m['cagr'] * 100:+.1f}% MDD{m['max_drawdown'] * 100:.1f}% "
                f"Sharpe{m['sharpe']:.2f} 笔数{m.get('n_trades', 0)} | "
                f"加仓{s['sz_add']}次 胜率{(s['sz_win'] / s['sz_add'] * 100 if s['sz_add'] else 0):.0f}% "
                f"单次{s['sz_avg_pnl'] * 100 if s['sz_pnl'] else 0:+.1f}%")
        print(line, flush=True)
        out.append(line)
with open(r"E:\mystockagent\output\deepdip_results.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
