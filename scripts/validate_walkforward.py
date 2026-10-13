# -*- coding: utf-8 -*-
"""深寒策略(cold)样本外验证与参数敏感性分析。

量化 "深寒买入策略年化14.6%" 的事后选参(过拟合)风险:
  1. 全样本参数网格: enter_below x exit_above 5x5 (cold 模式) —— 12/75 是平台还是孤峰
  2. Walk-forward 滚动验证(扩展窗5折): 每折训练期网格选参 → 测试期验证,
     测试段权益链式相接, 与固定12/75全样本 / 等权持有 / 沪深300 对比, 量化衰减
  3. 温度权重敏感性: 5种五维权重方案重算温度后跑 cold 12/75
  4. 成本压力测试: cost_rate 0.05% ~ 0.5%
  5. 全部结果写入 output/validation_wf.md + output/wf_*.csv

用法:
    python scripts/validate_walkforward.py           # 全量(25x6次回测, 约10-15分钟)
    python scripts/validate_walkforward.py --quick   # 小网格+只跑折1折2(自测)
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401  (加入 .libs)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import data as cdata  # noqa: E402
from cycle_system.backtest import (  # noqa: E402
    benchmark_equal_weight, benchmark_index, compute_metrics, run_backtest)
from cycle_system.cycle_engine import compute_temperature  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PKL = os.path.join(config.DATA_DIR, "enriched.pkl")
OUT = config.OUTPUT_DIR

BASE_ENTER, BASE_EXIT = 12, 75          # 待验证的默认阈值（事后选参的嫌疑对象）
COST_DEFAULT = 0.0015
WF_START = "2017-01-01"                 # walk-forward 测试链起点（折1测试起点）

GRID_ENTER = [8, 10, 12, 15, 20]
GRID_EXIT = [65, 70, 75, 80, 85]
QUICK_ENTER = [10, 12]
QUICK_EXIT = [70, 75]

# (train_end, test_start, test_end)；test_end=None 表示到数据末尾（扩展窗）
FOLDS = [
    ("2016-12-31", "2017-01-01", "2018-12-31"),
    ("2018-12-31", "2019-01-01", "2020-12-31"),
    ("2020-12-31", "2021-01-01", "2022-12-31"),
    ("2022-12-31", "2023-01-01", "2024-12-31"),
    ("2024-12-31", "2025-01-01", None),
]

# 温度五维权重方案: (标签, params)。维度顺序: 价格/动量/估值/广度/量能
WEIGHT_SCHEMES = [
    ("默认 25/25/25/15/10", dict(w_price=25, w_momentum=25, w_valuation=25,
                                 w_breadth=15, w_volume=10)),
    ("等权 20/20/20/20/20", dict(w_price=20, w_momentum=20, w_valuation=20,
                                 w_breadth=20, w_volume=20)),
    ("价格估值重 35/15/35/7.5/7.5", dict(w_price=35, w_momentum=15, w_valuation=35,
                                         w_breadth=7.5, w_volume=7.5)),
    ("动量重 15/40/20/12.5/12.5", dict(w_price=15, w_momentum=40, w_valuation=20,
                                       w_breadth=12.5, w_volume=12.5)),
    ("纯价格估值 50/0/50/0/0", dict(w_price=50, w_momentum=0, w_valuation=50,
                                    w_breadth=0, w_volume=0)),
]

COST_LEVELS = [0.0005, 0.0015, 0.003, 0.005]

T0 = time.time()
_BT_N = [0]


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def bt(dfs, meta, cost_rate=COST_DEFAULT, **kw):
    """run_backtest(mode='cold') 包装: 打印进度, 返回完整结果 dict。"""
    _BT_N[0] += 1
    t = time.time()
    res = run_backtest(dfs, meta, mode="cold", cost_rate=cost_rate, **kw)
    m = res["metrics"]
    log(f"    [bt#{_BT_N[0]:>3}] {time.time() - t:5.1f}s  "
        f"CAGR={m['cagr'] * 100:6.2f}%  Sharpe={m['sharpe']:5.2f}  "
        f"MDD={m['max_drawdown'] * 100:6.1f}%  笔数={m['n_trades']}")
    return res


# ---------------- 1. 参数网格 ----------------

def run_grid(dfs, meta, enters, exits, end=None):
    """在 dfs 上跑 enter x exit 网格（end 用于折内训练期截断）。"""
    sharpe = pd.DataFrame(np.nan, index=enters, columns=exits)
    cagr = pd.DataFrame(np.nan, index=enters, columns=exits)
    rows = []
    for e in enters:
        for x in exits:
            res = bt(dfs, meta, strat_params={"enter_below": e, "exit_above": x}, end=end)
            m = res["metrics"]
            sharpe.loc[e, x] = m["sharpe"]
            cagr.loc[e, x] = m["cagr"]
            rows.append({"enter_below": e, "exit_above": x, "sharpe": m["sharpe"],
                         "cagr": m["cagr"], "total_return": m["total_return"],
                         "max_drawdown": m["max_drawdown"], "n_trades": m["n_trades"],
                         "win_rate": m["win_rate"]})
    return sharpe, cagr, pd.DataFrame(rows)


def pick_best(sharpe, cagr):
    """网格选参: Sharpe 最高, 平手取 CAGR 高。返回 (enter, exit)。"""
    df = pd.DataFrame({"sharpe": sharpe.stack(), "cagr": cagr.stack()})
    (e, x) = df.sort_values(["sharpe", "cagr"], ascending=False).index[0]
    return int(e), int(x)


def plateau_check(sharpe, e0=BASE_ENTER, x0=BASE_EXIT, tol=0.15):
    """12/75 与网格相邻格的 Sharpe 差是否都 < tol（平台）还是存在孤立尖峰。"""
    enters, exits = list(sharpe.index), list(sharpe.columns)
    base = float(sharpe.loc[e0, x0])
    neigh = []
    for de, dx, tag in [(-1, 0, "enter降1档"), (1, 0, "enter升1档"),
                        (0, -1, "exit降1档"), (0, 1, "exit升1档")]:
        i, j = enters.index(e0) + de, exits.index(x0) + dx
        if 0 <= i < len(enters) and 0 <= j < len(exits):
            neigh.append({"方向": tag, "格子": f"({enters[i]},{exits[j]})",
                          "sharpe": float(sharpe.iloc[i, j]),
                          "diff": abs(base - float(sharpe.iloc[i, j]))})
    diffs = [n["diff"] for n in neigh] or [np.nan]
    max_diff = float(np.nanmax(diffs))
    rank = int((sharpe.values > base).sum()) + 1     # 12/75 在全部格子中的排名
    best_cell = sharpe.stack().idxmax()
    return {
        "base": base, "neighbors": neigh, "max_diff": max_diff,
        "is_plateau": bool(max_diff < tol), "tol": tol, "rank": rank,
        "n_cells": int(sharpe.size),
        "best_cell": (int(best_cell[0]), int(best_cell[1])),
        "best_sharpe": float(sharpe.stack().max()),
    }


# ---------------- 2. Walk-forward ----------------

def walk_forward(dfs, meta, enters, exits, folds):
    """扩展窗 walk-forward: 每折训练期网格选参 → 测试期验证。

    返回 (wf_equity, wf_metrics, 折明细 DataFrame, 训练网格明细 DataFrame)。
    """
    seg_levels = []       # 各测试段权益曲线（链式相接）
    prev_level = 1.0
    fold_rows, grid_rows = [], []
    for k, (tr_end, te_start, te_end) in enumerate(folds, 1):
        log(f"  [折{k}] 训练期 ≤{tr_end}: 网格 {len(enters)}x{len(exits)} ...")
        sh, cg, rows = run_grid(dfs, meta, enters, exits, end=tr_end)
        rows = rows.copy()
        rows.insert(0, "fold", k)
        grid_rows.append(rows)
        be, bx = pick_best(sh, cg)
        log(f"  [折{k}] 选中 enter<{be} / exit>{bx} "
            f"(训练Sharpe={sh.loc[be, bx]:.2f}) → 测试 {te_start}~{te_end or '末尾'}")
        res = bt(dfs, meta, strat_params={"enter_below": be, "exit_above": bx},
                 start=te_start, end=te_end)
        res_fix = bt(dfs, meta, strat_params={"enter_below": BASE_ENTER,
                                              "exit_above": BASE_EXIT},
                     start=te_start, end=te_end)
        m, mf = res["metrics"], res_fix["metrics"]
        seg = res["equity"]["equity"]
        seg = seg / seg.iloc[0]   # 段内归一: 剔除分段边界的人工建仓成本(实盘换折会持仓续接, 不重进场)
        seg_levels.append(seg * prev_level)
        prev_level = float(seg_levels[-1].iloc[-1])
        fold_rows.append({
            "fold": k, "train_end": tr_end,
            "test_start": te_start, "test_end": te_end or "末尾",
            "sel_enter": be, "sel_exit": bx,
            "train_sharpe": float(sh.loc[be, bx]), "train_cagr": float(cg.loc[be, bx]),
            "test_cagr": m["cagr"], "test_sharpe": m["sharpe"],
            "test_max_dd": m["max_drawdown"], "test_total": m["total_return"],
            "test_n_trades": m["n_trades"], "test_win_rate": m["win_rate"],
            "fixed_cagr": mf["cagr"], "fixed_sharpe": mf["sharpe"],
            "fixed_max_dd": mf["max_drawdown"],
        })
    wf_eq = pd.concat(seg_levels).sort_index()
    wf_equity = pd.DataFrame({"equity": wf_eq})
    wf_metrics = compute_metrics(wf_equity, None, None)
    return wf_equity, wf_metrics, pd.DataFrame(fold_rows), pd.concat(grid_rows, ignore_index=True)


# ---------------- 3. 权重敏感性 ----------------

def rebuild_dfs(dfs, wparams):
    """对全部板块用新权重重算 temp（其余列不动）。"""
    out = {}
    for name, df in dfs.items():
        df2 = df.copy()
        df2["temp"] = compute_temperature(df2, dict(wparams))
        out[name] = df2
    return out


def weight_sensitivity(dfs, meta):
    rows, max_temp_shift = [], 0.0
    for tag, wp in WEIGHT_SCHEMES:
        dfs2 = rebuild_dfs(dfs, wp)
        if "默认" in tag:  # 基线自检: 重算温度与富化温度的偏差
            for name in dfs:
                d = (dfs2[name]["temp"] - dfs[name]["temp"]).abs().max()
                max_temp_shift = max(max_temp_shift, float(d))
        res = bt(dfs2, meta, strat_params={"enter_below": BASE_ENTER,
                                           "exit_above": BASE_EXIT})
        m = res["metrics"]
        rows.append({"方案": tag, "cagr": m["cagr"], "sharpe": m["sharpe"],
                     "max_drawdown": m["max_drawdown"], "ann_vol": m["ann_vol"],
                     "n_trades": m["n_trades"], "total_return": m["total_return"]})
    return pd.DataFrame(rows), max_temp_shift


# ---------------- 4. 成本压力 ----------------

def cost_stress(dfs, meta):
    rows = []
    for c in COST_LEVELS:
        res = bt(dfs, meta, cost_rate=c, strat_params={"enter_below": BASE_ENTER,
                                                       "exit_above": BASE_EXIT})
        m = res["metrics"]
        rows.append({"cost_rate": c, "cost_pct": f"{c * 100:.2f}%", "cagr": m["cagr"],
                     "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"],
                     "n_trades": m["n_trades"], "total_return": m["total_return"]})
    return pd.DataFrame(rows)


# ---------------- 输出工具 ----------------

def fmt_pct(v, digits=1):
    return "-" if v is None or (isinstance(v, float) and not np.isfinite(v)) \
        else f"{v * 100:.{digits}f}%"


def fmt(v, digits=2):
    return "-" if v is None or (isinstance(v, float) and not np.isfinite(v)) \
        else f"{v:.{digits}f}"


def md_table(rows, headers):
    """手写 markdown 表（避免依赖 tabulate）。rows=字符串二维列表。"""
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def matrix_md(mat, e0=BASE_ENTER, x0=BASE_EXIT, digits=2, scale=1.0, unit=""):
    """网格矩阵 → markdown 表, 12/75 单元格加 ★。"""
    headers = ["enter\\exit"] + [str(c) for c in mat.columns]
    rows = []
    for e in mat.index:
        cells = []
        for x in mat.columns:
            v = mat.loc[e, x]
            s = f"{v * scale:.{digits}f}{unit}" if np.isfinite(v) else "-"
            if e == e0 and x == x0:
                s += " ★"
            cells.append(s)
        rows.append([f"**{e}**" if e == e0 else str(e)] + cells)
    return md_table(rows, headers)


def metrics_row(tag, window, equity_df):
    m = compute_metrics(equity_df, None, None)
    return {"方案": tag, "区间": window, "总收益": m["total_return"],
            "年化": m["cagr"], "Sharpe": m["sharpe"],
            "最大回撤": m["max_drawdown"], "年化波动": m["ann_vol"]}


def make_figures(sharpe, curves, tag):
    """两张图: 网格Sharpe热力图 + 权益曲线对比。失败不阻塞报告。"""
    try:
        plt.rcParams["font.sans-serif"] = config.PLOT_FONTS
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(figsize=(7.2, 5.2))
        arr = sharpe.values.astype(float)
        im = ax.imshow(arr, cmap="RdYlGn", aspect="auto")
        ax.set_xticks(range(len(sharpe.columns)), [str(c) for c in sharpe.columns])
        ax.set_yticks(range(len(sharpe.index)), [str(e) for e in sharpe.index])
        ax.set_xlabel("exit_above")
        ax.set_ylabel("enter_below")
        ax.set_title("全样本参数网格 Sharpe（★=默认12/75）")
        for i in range(arr.shape[0]):
            for j in range(arr.shape[1]):
                mark = " ★" if (sharpe.index[i] == BASE_ENTER
                                and sharpe.columns[j] == BASE_EXIT) else ""
                ax.text(j, i, f"{arr[i, j]:.2f}{mark}", ha="center", va="center",
                        fontsize=9)
        fig.colorbar(im, ax=ax, label="Sharpe")
        fig.tight_layout()
        p1 = os.path.join(OUT, f"wf_grid_heatmap_{tag}.png")
        fig.savefig(p1, dpi=130)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(9.5, 5.2))
        for label, eq in curves:
            ax.plot(eq.index, eq["equity"], label=label, linewidth=1.4)
        ax.set_title("Walk-forward 链 vs 固定12/75 vs 基准（同期）")
        ax.set_ylabel("净值")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        p2 = os.path.join(OUT, f"wf_equity_{tag}.png")
        fig.savefig(p2, dpi=130)
        plt.close(fig)
        return os.path.basename(p1), os.path.basename(p2)
    except Exception as e:  # 画图失败不影响数据结论
        log(f"[warn] 画图失败: {e}")
        return None, None


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser(description="深寒策略样本外验证与参数敏感性分析")
    ap.add_argument("--quick", action="store_true",
                    help="小网格(enter 10/12, exit 70/75)+只跑折1折2, 自测用")
    args = ap.parse_args()
    tag = "quick" if args.quick else "full"
    enters = QUICK_ENTER if args.quick else GRID_ENTER
    exits = QUICK_EXIT if args.quick else GRID_EXIT
    folds = FOLDS[:2] if args.quick else FOLDS

    log(f"[load] 读取 {PKL}")
    obj = pd.read_pickle(PKL)
    dfs, meta = obj["dfs"], obj["meta"]
    d0 = min(df.index[0] for df in dfs.values())
    d1 = max(df.index[-1] for df in dfs.values())
    log(f"[data] {len(dfs)} 板块, {d0.date()} ~ {d1.date()}")

    # ---- 1. 全样本参数网格 ----
    log(f"[1/4] 全样本参数网格 {len(enters)}x{len(exits)} (cold, cost={COST_DEFAULT})")
    sharpe, cagr, grid_rows = run_grid(dfs, meta, enters, exits)
    pl = plateau_check(sharpe)
    log(f"[1/4] 12/75 Sharpe={pl['base']:.2f}, 网格排名 {pl['rank']}/{pl['n_cells']}, "
        f"相邻最大差 {pl['max_diff']:.2f} → {'平台' if pl['is_plateau'] else '孤峰'}")

    # ---- 2. Walk-forward ----
    log(f"[2/4] Walk-forward 扩展窗 {len(folds)} 折 ...")
    wf_equity, wf_m, fold_df, wf_train_grid = walk_forward(dfs, meta, enters, exits, folds)
    wf_end = str(wf_equity.index[-1].date())
    log(f"[2/4] WF链 {WF_START}~{wf_end}: 年化={wf_m['cagr'] * 100:.2f}% "
        f"Sharpe={wf_m['sharpe']:.2f} MDD={wf_m['max_drawdown'] * 100:.1f}%")

    # 固定 12/75 两个口径 + 基准
    log("[2/4] 固定 12/75 全样本 / WF同窗 + 等权 / 沪深300 ...")
    fixed_full = bt(dfs, meta, strat_params={"enter_below": BASE_ENTER,
                                             "exit_above": BASE_EXIT})
    fixed_wfwin = bt(dfs, meta, strat_params={"enter_below": BASE_ENTER,
                                              "exit_above": BASE_EXIT},
                     start=WF_START, end=wf_end)
    ew_full = benchmark_equal_weight(dfs)
    ew_wf = benchmark_equal_weight(dfs, start=WF_START)
    try:
        bench = cdata.get_benchmark()
        bench_close = bench.set_index("date")["close"]
        hs_full = benchmark_index(bench_close, dfs)
        hs_wf = benchmark_index(bench_close, dfs, start=WF_START)
        hs_note = ""
    except Exception as e:
        log(f"[warn] 沪深300 基准获取失败({e}), 报告中略去")
        hs_full = hs_wf = None
        hs_note = f"（沪深300数据获取失败: {e}）"

    comp_rows = [
        metrics_row("固定12/75 全样本", f"{d0.date()}~{d1.date()}", fixed_full["equity"]),
        metrics_row("固定12/75 WF同窗", f"{WF_START}~{wf_end}", fixed_wfwin["equity"]),
        metrics_row("Walk-forward 链", f"{WF_START}~{wf_end}", wf_equity),
        metrics_row("等权持有", f"{WF_START}~{wf_end}", ew_wf),
        metrics_row("等权持有", f"{d0.date()}~{d1.date()}", ew_full),
    ]
    if hs_wf is not None:
        comp_rows += [
            metrics_row("沪深300", f"{WF_START}~{wf_end}", hs_wf),
            metrics_row("沪深300", f"{d0.date()}~{d1.date()}", hs_full),
        ]
    comp = pd.DataFrame(comp_rows)
    decay = fixed_full["metrics"]["cagr"] - wf_m["cagr"]                      # 题设定义
    decay_samewin = fixed_wfwin["metrics"]["cagr"] - wf_m["cagr"]             # 同窗口径参考

    # ---- 3. 权重敏感性 ----
    log("[3/4] 温度权重敏感性 (5 方案重算温度 → cold 12/75) ...")
    wsen, temp_shift = weight_sensitivity(dfs, meta)
    # ---- 4. 成本压力 ----
    log("[4/4] 成本压力测试 ...")
    cost = cost_stress(dfs, meta)

    # ---- 落盘 CSV ----
    os.makedirs(OUT, exist_ok=True)
    grid_name = "wf_grid_full.csv" if not args.quick else "wf_grid_quick.csv"
    pd.concat({"sharpe": sharpe, "cagr": cagr}, axis=1).to_csv(os.path.join(OUT, grid_name))
    sharpe.to_csv(os.path.join(OUT, f"wf_grid_sharpe_{tag}.csv"))
    cagr.to_csv(os.path.join(OUT, f"wf_grid_cagr_{tag}.csv"))
    grid_rows.to_csv(os.path.join(OUT, f"wf_grid_rows_{tag}.csv"), index=False)
    fold_df.to_csv(os.path.join(OUT, f"wf_folds_{tag}.csv"), index=False)
    wf_train_grid.to_csv(os.path.join(OUT, f"wf_train_grids_{tag}.csv"), index=False)
    wf_equity.to_csv(os.path.join(OUT, f"wf_equity_chain_{tag}.csv"))
    comp.to_csv(os.path.join(OUT, f"wf_comparison_{tag}.csv"), index=False)
    wsen.to_csv(os.path.join(OUT, f"weight_sensitivity_{tag}.csv"), index=False)
    cost.to_csv(os.path.join(OUT, f"cost_stress_{tag}.csv"), index=False)

    # ---- 图 ----
    curves = [("固定12/75(WF同窗)", fixed_wfwin["equity"]),
              ("Walk-forward 链", wf_equity),
              ("等权持有", ew_wf)]
    if hs_wf is not None:
        curves.append(("沪深300", hs_wf))
    p_heat, p_eq = make_figures(sharpe, curves, tag)

    # ---- Markdown 报告 ----
    rep = []
    rep.append("# 深寒策略样本外验证与参数敏感性报告\n")
    rep.append(f"- 生成: {pd.Timestamp.now():%Y-%m-%d %H:%M} | 模式: {'quick 自测' if args.quick else '全量'}"
               f" | 回测次数: {_BT_N[0]} | 耗时 {time.time() - T0:.0f}s")
    rep.append(f"- 数据: enriched.pkl, {len(dfs)} 板块, {d0.date()} ~ {d1.date()}"
               f" | 成本默认单边 {COST_DEFAULT * 100:.2f}% | cold 模式(等权{config.STRATEGY['cold_max_positions']}仓,"
               f"移动止损{config.STRATEGY['trailing_stop']['cold'] * 100:.0f}%)\n")

    rep.append("## 1. 全样本参数网格（enter_below × exit_above, mode=cold）\n")
    rep.append("### Sharpe 矩阵（行=enter, 列=exit, ★=默认 12/75）\n")
    rep.append(matrix_md(sharpe) + "\n")
    rep.append("### CAGR 矩阵（%）\n")
    rep.append(matrix_md(cagr, digits=1, scale=100.0, unit="%") + "\n")
    rep.append(f"### 平台/孤峰判断（容差 {pl['tol']} Sharpe）\n")
    n_rows = [[n["方向"], n["格子"], fmt(n["sharpe"]), fmt(n["diff"])] for n in pl["neighbors"]]
    rep.append("12/75 与相邻格子的 Sharpe 对比:\n")
    rep.append(md_table(n_rows, ["相邻方向", "格子", "Sharpe", "|差|"]) + "\n")
    verdict = ("**平台**（相邻格最大 Sharpe 差 %.2f < %.2f, 12/75 的成绩不是靠卡某一个格子）"
               if pl["is_plateau"] else
               "**孤峰**（相邻格最大 Sharpe 差 %.2f ≥ %.2f, 12/75 处于孤立高点, 事后选参风险高）")
    rep.append(f"判断: 12/75 Sharpe={fmt(pl['base'])}, 在 {pl['n_cells']} 个格子中排第 {pl['rank']} 名; "
               f"最优格 {pl['best_cell']} Sharpe={fmt(pl['best_sharpe'])}。→ "
               + (verdict % (pl["max_diff"], pl["tol"]) if pl["is_plateau"]
                  else verdict % (pl["max_diff"], pl["tol"])) + "\n")

    rep.append(f"## 2. Walk-forward 滚动验证（扩展窗, {len(folds)} 折）\n")
    rep.append("每折: 训练期跑同一网格 → 选 Sharpe 最高（平手取 CAGR 高）→ 测试期用选中参数回测"
               "（同一折同时给出固定 12/75 的测试期成绩作对照）。\n")
    f_rows = []
    for _, r in fold_df.iterrows():
        f_rows.append([f"折{int(r['fold'])}", f"≤{r['train_end']}",
                       f"{r['test_start']}~{r['test_end']}",
                       f"{int(r['sel_enter'])}/{int(r['sel_exit'])}",
                       fmt(r["train_sharpe"]), fmt_pct(r["test_cagr"]), fmt(r["test_sharpe"]),
                       fmt_pct(r["test_max_dd"]), fmt_pct(r["test_total"]),
                       str(int(r["test_n_trades"])), fmt_pct(r["test_win_rate"]),
                       fmt_pct(r["fixed_cagr"]), fmt(r["fixed_sharpe"])])
    rep.append(md_table(f_rows, ["折", "训练期", "测试期", "选中参数(e/x)", "训练Sharpe",
                                 "测试年化", "测试Sharpe", "测试MDD", "测试总收益", "笔数",
                                 "测试胜率", "固定12/75年化", "固定Sharpe"]) + "\n")
    sel_list = ", ".join(f"{int(r['sel_enter'])}/{int(r['sel_exit'])}"
                         for _, r in fold_df.iterrows())
    rep.append(f"各折选中参数: {sel_list}（默认 12/75）\n")

    rep.append("### 测试段权益链（各段从1起、链式相接） vs 对照\n")
    c_rows = [[r["方案"], r["区间"], fmt_pct(r["总收益"]), fmt_pct(r["年化"]), fmt(r["Sharpe"]),
               fmt_pct(r["最大回撤"]), fmt_pct(r["年化波动"])] for _, r in comp.iterrows()]
    rep.append(md_table(c_rows, ["方案", "区间", "总收益", "年化", "Sharpe", "最大回撤", "年化波动"]) + "\n")
    rep.append(f"**过拟合衰减（题设口径）= 固定12/75全样本年化 − WF链年化 = "
               f"{fmt_pct(fixed_full['metrics']['cagr'])} − {fmt_pct(wf_m['cagr'])} = "
               f"**{decay * 100:+.1f} 个百分点**\n")
    rep.append(f"参考（同窗口径）: WF同窗固定12/75 年化 {fmt_pct(fixed_wfwin['metrics']['cagr'])} − "
               f"WF链 {fmt_pct(wf_m['cagr'])} = {decay_samewin * 100:+.1f} 个百分点"
               f"（剔除'时间段不同'的影响后的纯选参衰减）\n")
    if p_eq:
        rep.append(f"![权益曲线]({p_eq})\n")

    rep.append("## 3. 温度权重敏感性（5 种权重方案重算温度 → cold 12/75）\n")
    w_rows = [[r["方案"], fmt_pct(r["cagr"]), fmt(r["sharpe"]), fmt_pct(r["max_drawdown"]),
               fmt_pct(r["ann_vol"]), str(int(r["n_trades"])), fmt_pct(r["total_return"])]
              for _, r in wsen.iterrows()]
    rep.append(md_table(w_rows, ["权重方案(价/动/估/广/量)", "年化", "Sharpe", "MDD",
                                 "年化波动", "笔数", "总收益"]) + "\n")
    wc = wsen["cagr"]
    rep.append(f"温度重算自检: 默认方案重算温度与富化缓存温度最大绝对差 {temp_shift:.2e}。"
               f"五方案年化区间 [{fmt_pct(wc.min())}, {fmt_pct(wc.max())}], 极差 "
               f"{fmt_pct(wc.max() - wc.min())}; Sharpe 区间 "
               f"[{fmt(wsen['sharpe'].min())}, {fmt(wsen['sharpe'].max())}]。\n")

    rep.append("## 4. 成本压力测试（cold 12/75, 全样本）\n")
    k_rows = [[r["cost_pct"], fmt_pct(r["cagr"]), fmt(r["sharpe"]),
               fmt_pct(r["max_drawdown"]), str(int(r["n_trades"])), fmt_pct(r["total_return"])]
              for _, r in cost.iterrows()]
    rep.append(md_table(k_rows, ["单边成本", "年化", "Sharpe", "MDD", "笔数", "总收益"]) + "\n")
    kc = cost["cagr"]
    rep.append(f"成本 {cost['cost_pct'].iloc[0]} → {cost['cost_pct'].iloc[-1]} "
               f"年化从 {fmt_pct(kc.iloc[0])} 降到 {fmt_pct(kc.iloc[-1])}，"
               f"降幅 {fmt_pct(kc.iloc[0] - kc.iloc[-1])}。\n")

    # ---- 结论（全部由上方数据推出） ----
    rep.append("## 5. 结论\n")
    items = []
    items.append(f"**参数形状**: 12/75 在 5x5 网格中排第 {pl['rank']}/{pl['n_cells']} 名"
                 f"（Sharpe {fmt(pl['base'])}，最优格 {pl['best_cell']} = {fmt(pl['best_sharpe'])}），"
                 f"与相邻格最大 Sharpe 差 {pl['max_diff']:.2f}，判定为"
                 f"{'平台（相邻格差全部 < %.2f）' % pl['tol'] if pl['is_plateau'] else '孤峰（存在相邻格差 ≥ %.2f）' % pl['tol']}。")
    items.append(f"**过拟合衰减**: 固定12/75全样本年化 {fmt_pct(fixed_full['metrics']['cagr'])}，"
                 f"walk-forward 链年化 {fmt_pct(wf_m['cagr'])}，衰减 "
                 f"{decay * 100:+.1f} 个百分点（同窗口径 {decay_samewin * 100:+.1f} 个百分点）。"
                 f"各折 WF 选中参数（{sel_list}）与默认 12/75 的偏离情况见第2节表。")
    w_ok = bool((wc > 0).all())
    items.append(f"**权重敏感性**: 5 种温度权重方案年化 {fmt_pct(wc.min())} ~ {fmt_pct(wc.max())}"
                 f"（极差 {fmt_pct(wc.max() - wc.min())}），{'全部为正' if w_ok else '存在负值方案'}，"
                 f"Sharpe {fmt(wsen['sharpe'].min())} ~ {fmt(wsen['sharpe'].max())}，"
                 f"结论对温度构成方式{'不敏感' if (w_ok and wsen['sharpe'].min() > 0.5) else '有一定敏感'}。")
    items.append(f"**成本压力**: 单边成本 {cost['cost_pct'].iloc[0]}~{cost['cost_pct'].iloc[-1]}，"
                 f"年化 {fmt_pct(kc.iloc[0])} → {fmt_pct(kc.iloc[-1])}"
                 f"（每提高 0.1% 成本约损失年化 "
                 f"{(kc.iloc[0] - kc.iloc[-1]) / (cost['cost_rate'].iloc[-1] - cost['cost_rate'].iloc[0]) * 0.001 * 100:.1f} 个百分点），"
                 f"默认 0.15% 假设下年化 {fmt_pct(kc.iloc[1])}。")
    ew_row = comp[comp["方案"] == "等权持有"].iloc[0]
    items.append(f"**相对基准**: WF同窗内策略（固定 {fmt_pct(fixed_wfwin['metrics']['cagr'])} / WF链 "
                 f"{fmt_pct(wf_m['cagr'])}）对等权持有（{fmt_pct(ew_row['年化'])}）"
                 f"{'仍有超额' if fixed_wfwin['metrics']['cagr'] > ew_row['年化'] else '超额消失'}"
                 f"{hs_note}。")
    # 总判决
    robust = pl["is_plateau"] and decay < 0.03 and decay_samewin < 0.03 and w_ok
    if robust:
        judge = ("**总体判断: 稳健**——参数是平台、walk-forward 衰减小于 3 个百分点、"
                 "温度权重方案全部为正收益，'年化14.6%' 的核心结论大概率不是过拟合产物。")
    elif pl["is_plateau"] and decay < 0.06 and w_ok:
        judge = ("**总体判断: 部分稳健**——参数形状尚可、权重方案全部为正，但 walk-forward "
                 f"衰减 {decay * 100:.1f} 个百分点不可忽略：全样本数字应打折看待，"
                 "实盘预期应向 WF 链年化靠拢。")
    else:
        judge = ("**总体判断: 存在明显过拟合证据**——" +
                 ("参数是孤峰; " if not pl["is_plateau"] else "") +
                 (f"walk-forward 衰减 {decay * 100:.1f} 个百分点; " if decay >= 0.06 else "") +
                 ("存在负收益权重方案。" if not w_ok else "") +
                 " '年化14.6%' 不能作为实盘预期。")
    items.append(judge)
    for it in items:
        rep.append(f"- {it}")
    rep.append("")
    rep.append(f"产物: {grid_name}（Sharpe+CAGR 矩阵）, wf_grid_sharpe/cagr/rows_{tag}.csv, "
               f"wf_folds_{tag}.csv, wf_train_grids_{tag}.csv, wf_equity_chain_{tag}.csv, "
               f"wf_comparison_{tag}.csv, weight_sensitivity_{tag}.csv, cost_stress_{tag}.csv"
               + (f", {p_heat}, {p_eq}" if p_heat and p_eq else "") + "\n")

    report_name = "validation_wf_quick.md" if args.quick else "validation_wf.md"
    report_path = os.path.join(OUT, report_name)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(rep))
    log(f"[done] 报告 → {report_path} | 回测 {_BT_N[0]} 次, 耗时 {time.time() - T0:.0f}s")


if __name__ == "__main__":
    main()
