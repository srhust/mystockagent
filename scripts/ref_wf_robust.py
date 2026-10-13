# -*- coding: utf-8 -*-
"""区分检验: 网格最优选参(脆弱) vs 邻域平滑稳健选参, 谁导致的 WF 衰减?

用 wf_train_grids_full.csv 里各折训练网格, 比较两种选参规则的测试期链式表现:
- 朴素: 训练 Sharpe 最高格(原脚本规则, 已知链式年化 2.9%)
- 稳健: 3x3 邻域平均 Sharpe 最高的内部格(抗单格噪声)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system.backtest import run_backtest  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]
g = pd.read_csv(os.path.join(config.OUTPUT_DIR, "wf_train_grids_full.csv"))
FOLDS = [("2017-01-01", "2018-12-31"), ("2019-01-01", "2020-12-31"),
         ("2021-01-01", "2022-12-31"), ("2023-01-01", "2024-12-31"),
         ("2025-01-01", None)]
ENTERS, EXITS = [8, 10, 12, 15, 20], [65, 70, 75, 80, 85]


def chain(picks):
    seg = 1.0
    for (e, x), (ts, te) in zip(picks, FOLDS):
        b = run_backtest(dfs, meta, mode="cold", start=ts, end=te,
                         strat_params={"enter_below": e, "exit_above": x})
        eq = b["equity"]["equity"]
        seg *= eq.iloc[-1] / eq.iloc[0]
    yrs = 9.8
    return seg - 1, seg ** (1 / yrs) - 1


print("=== 各折两种选参对比 ===")
robust_picks = []
for k in range(1, 6):
    sub = g[g["fold"] == k]
    sh = sub.pivot(index="enter_below", columns="exit_above", values="sharpe")
    # 朴素 = 最高格
    naive = sub.sort_values(["sharpe", "cagr"], ascending=False).iloc[0]
    # 稳健 = 3x3 邻域平均最高的内部格(排除边缘格 eb8/20, xa65/85)
    best, be, bx = None, None, None
    for i, e in enumerate(ENTERS):
        for j, x in enumerate(EXITS):
            if not (1 <= i <= 3 and 1 <= j <= 3):
                continue
            nb = sh.iloc[i - 1:i + 2, j - 1:j + 2].values
            m = nb[~pd.isna(nb)].mean()
            if best is None or m > best:
                best, be, bx = m, e, x
    robust_picks.append((be, bx))
    print(f"  折{k}: 朴素 {int(naive['enter_below'])}/{int(naive['exit_above'])}"
          f"(训练S {naive['sharpe']:.2f})  vs  稳健 {be}/{bx}(邻域均值 {best:.2f})")

for label, picks in [("朴素网格最优", [(10, 85), (10, 70), (10, 65), (10, 65), (10, 65)]),
                     ("邻域平滑稳健", robust_picks),
                     ("固定12/75", [(12, 75)] * 5)]:
    tot, ann = chain(picks)
    picks_s = " ".join(f"{e}/{x}" for e, x in picks)
    print(f"\n{label}: 链式总收益 {tot * 100:+.1f}%, 年化 {ann * 100:+.1f}%  [{picks_s}]")
