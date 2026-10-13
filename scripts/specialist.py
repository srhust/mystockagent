# -*- coding: utf-8 -*-
"""
单行业专户模拟器 + 行业分层差异化研究。

专户规则(基础版): 每行业独立账户 100 资金, temp<12 买 40 → 价格 -15% 加 30 → -30% 加 30
(各一次), temp>75 全部清仓。
差异化测试:
- 出场阈值: 70 / 75 / 80 按类别
- 加仓档距: 固定 -15/-30 vs 波动率缩放 -0.5σ/-1.0σ (σ=入场日120日年化波动)
输出: output/specialist_report.md (含每板块当前账户状态卡)
"""
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import strategy as strat  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)
obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]


def simulate_account(df, enter=12, exit_=75, unit_fracs=(0.4, 0.3, 0.3),
                     add_levels=(-0.15, -0.30), vol_scaled=False):
    """单板块专户回测: temp<enter 买 unit_fracs[0] → 依次加仓 → temp>exit 清仓。"""
    pos = strat.position_target(df, mode="cold", enter_below=enter, exit_above=exit_).values
    close = df["close"].values
    ret = df["close"].pct_change()
    sig120 = ret.rolling(120).std() * (120 ** 0.5)
    n = len(pos)
    trades = []
    i = 0
    while i < n:
        if pos[i] == 1 and (i == 0 or pos[i - 1] == 0):
            e0 = close[i]
            s = sig120.iloc[i] if np.isfinite(sig120.iloc[i]) else 0.20
            lv = (-0.5 * s, -1.0 * s) if vol_scaled else add_levels
            units = [(unit_fracs[0] * 100, e0)]
            used = 0
            j = i + 1
            while j < n and pos[j] == 1:
                if used < len(lv) and np.isfinite(close[j]) and close[j] <= e0 * (1 + lv[used]):
                    units.append((unit_fracs[used + 1] * 100, close[j]))
                    used += 1
                j += 1
            end = j - 1
            if end > i + 5:
                tot_u = sum(u for u, _ in units)
                avg_c = sum(u * p for u, p in units) / tot_u
                hold = close[i:end + 1]
                fl = np.array([sum(u * (p - pi) for u, pi in units) for p in hold]) / (tot_u * avg_c)
                trades.append({"sector": "", "entry": df.index[i], "exit": df.index[end],
                               "days": end - i, "ret": float(hold[-1] / avg_c - 1.0),
                               "max_float_dd": float(fl.min()), "adds": used})
            i = j
        else:
            i += 1
    return trades


CATS = {}
for name, m in meta.items():
    CATS[name] = m.get("cat", "其他")

VARIANTS = [
    ("基础: 75出场+固定-15/-30", dict(exit_=75, vol_scaled=False)),
    ("80出场+固定-15/-30", dict(exit_=80, vol_scaled=False)),
    ("85出场+固定-15/-30", dict(exit_=85, vol_scaled=False)),
    ("70出场+固定-15/-30", dict(exit_=70, vol_scaled=False)),
    ("75出场+波动率档距", dict(exit_=75, vol_scaled=True)),
]

all_res = {}
print("[run] 模拟 30 板块 × 4 变体 ...", flush=True)
for name, df in dfs.items():
    for vn, kw in VARIANTS:
        tr = simulate_account(df, **kw)
        for t in tr:
            t["sector"] = name
            t["cat"] = CATS.get(name, "其他")
        all_res[(name, vn)] = tr

lines = ["# 单行业专户模拟: 行业分层差异化\n",
         f"- 生成: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} | 每行业独立账户100资金 | "
         "入场 temp<12 买40 → 加30 → 加30 | 卖出按变体",
         "- 注: 单行业口径无分散, 单笔浮亏会显著大于组合口径; 类别结论需 ≥3 行业支持\n",
         "## 一、按类别汇总（各变体）\n",
         "| 类别 | 变体 | 笔数 | 单笔均值 | 中位 | 胜率 | 最大浮亏均值 |", "|---|---|---|---|---|---|---|"]
cat_table = {}
for vn, _kw in VARIANTS:
    rows = [t for (n, v), ts in all_res.items() if v == vn for t in ts]
    dfv = pd.DataFrame(rows)
    if not len(dfv):
        continue
    for cat, g in dfv.groupby("cat"):
        if len(g) < 8:
            continue
        cat_table[(cat, vn)] = g
        lines.append(f"| {cat} | {vn} | {len(g)} | {g['ret'].mean() * 100:+.1f}% | "
                     f"{g['ret'].median() * 100:+.1f}% | {(g['ret'] > 0).mean() * 100:.0f}% | "
                     f"{g['max_float_dd'].mean() * 100:.1f}% |")

lines += ["\n## 二、类别最优变体（需邻档不劣才算稳健）\n"]
for cat in sorted(set(k[0] for k in cat_table)):
    best_v, best_r = None, -9e9
    for vn, _kw in VARIANTS:
        if (cat, vn) in cat_table:
            r = cat_table[(cat, vn)]["ret"].mean()
            if r > best_r:
                best_v, best_r = vn, r
    lines.append(f"- **{cat}**: 最优 {best_v}（单笔均值 {best_r * 100:+.1f}%）")

lines += ["\n## 三、每板块账户状态卡（截至最新交易日）\n",
          "| 板块 | 类别 | 温度 | 信号 | 账户状态 |", "|---|---|---|---|---|"]
for name, df in dfs.items():
    t = df["temp"].iloc[-1]
    p = strat.position_target(df, mode="cold").iloc[-1]
    if p == 1:
        run_start = df["temp"][df["temp"] < 12]
        e0 = df["close"].iloc[-len(df[df.index >= run_start.index[-1]]):] if len(run_start) else None
        state = "持仓中: 距-15%加档/75清仓执行"
    else:
        state = f"空仓: 等待 temp<12 (当前 {t:.0f})"
    lines.append(f"| {name} | {CATS.get(name, '')} | {t:.0f} | {'持仓' if p == 1 else '空仓'} | {state} |")

with open(os.path.join(config.OUTPUT_DIR, "specialist_report.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

tot = sum(len(v) for v in all_res.values())
print(f"[done] output/specialist_report.md | {tot} 笔专户交易")
