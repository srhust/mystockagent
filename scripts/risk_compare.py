# -*- coding: utf-8 -*-
"""组合级风控层对比回测：回撤控制 / 波动率目标 / 相关性聚类限额。

背景
----
深寒策略（温度<12 买入 / >75 卖出, mode=cold）全样本 CAGR 约 11-15%,
但最大回撤约 -40%。本脚本在不动策略本身的前提下, 对比三类**组合级风控层**
对回撤的改善与收益的牺牲:

  0 基线            run_backtest(mode="cold")
  1 +回撤控制        overlay_equity(w, rets)（回撤迟滞: -15%减半 / -30%清仓 / -8%恢复）
  2 +波动率目标      overlay_equity(w, rets, vol_target=V)（只开波动率缩放）
  3 +回撤+波动率     overlay_equity(w, rets, vol_target=V)（回撤迟滞 + 波动率目标一起）
  4 相关性聚类限额   全样本日收益相关矩阵 + 并查集(corr>阈值并簇),
                    meta 的 "cat" 换成簇编号 → run_backtest(per_category_max=2)
  5 聚类+回撤控制    方案4 的权重序列再过 overlay_equity(回撤迟滞)

方案2 实现说明: overlay_equity 中回撤迟滞与波动率目标在同一函数内, 回撤控制
没有"关闭"开关, 故以 dd_soft=dd_hard=-100%（组合回撤恒 > -100%, 状态机永不
触发）实现"只开波动率目标"; 方案3 才是回撤迟滞默认参数 + 波动率目标。

用法
----
    python scripts/risk_compare.py                      # 全量 6 方案（约2次回测+4次overlay）
    python scripts/risk_compare.py --corr-thresh 0.7 --vol-target 0.20

输出
----
    终端: 聚类结果表 + 与原分组差异 + 六方案对比表 + 结论
    output/risk_compare.md
    output/risk_curves.png（基线/方案1/方案4/方案5 权益曲线 + 回撤, 深回撤区间阴影）
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401  (加入 .libs)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import config  # noqa: E402
from cycle_system.backtest import run_backtest, overlay_equity  # noqa: E402

try:  # Windows 控制台中文输出
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PKL = os.path.join(config.DATA_DIR, "enriched.pkl")
MD_PATH = os.path.join(config.OUTPUT_DIR, "risk_compare.md")
PNG_PATH = os.path.join(config.OUTPUT_DIR, "risk_curves.png")

DD_OFF = -1.0   # 组合回撤恒 > -100%: 以此作 dd 阈值 = 关闭 overlay 的回撤状态机

SCHEME_NAMES = ["0 基线", "1 +回撤控制", "2 +波动率目标",
                "3 +回撤+波动率", "4 相关性聚类限额", "5 聚类+回撤控制"]


# ---------------- 并查集 + 相关性聚类 ----------------
class UnionFind:
    """最小并查集（仅路径压缩, n≈30 无需按秩合并）。"""

    def __init__(self, n):
        self.fa = list(range(n))

    def find(self, x):
        while self.fa[x] != x:
            self.fa[x] = self.fa[self.fa[x]]  # 路径压缩
            x = self.fa[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.fa[rb] = ra


def cluster_by_correlation(rets: pd.DataFrame, thresh: float):
    """日收益相关矩阵 + 并查集: corr > thresh 的板块对并入同簇。

    Returns
    -------
    corr     : DataFrame 板块相关矩阵
    clusters : [[板块, ...], ...] 簇编号按簇内板块数降序、首板块出现顺序稳定排列
    label    : {板块: 簇编号}
    """
    sectors = list(rets.columns)
    corr = rets.corr()
    n = len(sectors)
    uf = UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            c = corr.iloc[i, j]
            if np.isfinite(c) and c > thresh:
                uf.union(i, j)
    groups = {}
    for i, s in enumerate(sectors):
        groups.setdefault(uf.find(i), []).append(s)
    clusters = sorted(groups.values(), key=lambda g: (-len(g), sectors.index(g[0])))
    label = {s: k for k, g in enumerate(clusters) for s in g}
    return corr, clusters, label


def _intra_mean_corr(corr: pd.DataFrame, members) -> float:
    """簇内平均两两相关（单板块簇返回 NaN）。"""
    if len(members) < 2:
        return float("nan")
    sub = corr.loc[members, members].values
    iu = np.triu_indices(len(members), k=1)
    return float(np.mean(sub[iu]))


def cluster_diff_notes(meta: dict, clusters, label, corr_thresh, per_cat_max):
    """聚类结果 vs 原 cat 分组的差异说明（逐条文字）。"""
    orig = {}
    for s, m in meta.items():
        orig.setdefault(m.get("cat", "?"), []).append(s)
    notes = [f"原 {len(orig)} 个周期类别（{'、'.join(orig)}）→ 相关性>{corr_thresh:.2f} 并簇后为 "
             f"{len(clusters)} 个簇，同簇最多持有 {per_cat_max} 个板块（原规则: 同类别最多 {per_cat_max} 个）。"]
    n_merge = 0
    for k, members in enumerate(clusters):
        cats = sorted({meta.get(s, {}).get("cat", "?") for s in members})
        if len(members) >= 2 and len(cats) > 1:
            n_merge += 1
            notes.append(f"跨类别合并: 簇{k}（{'、'.join(members)}）横跨 {'、'.join(cats)} "
                         f"→ 这 {len(members)} 个板块同簇最多 {per_cat_max} 个。")
    n_split = 0
    for cat in sorted(orig):
        secs = orig[cat]
        ks = sorted({label[s] for s in secs})
        if len(ks) > 1:
            n_split += 1
            notes.append(f"类别拆散: 原{cat}（{len(secs)}板块）分散到 {len(ks)} 个簇"
                         f"（{'、'.join('簇%d' % k for k in ks)}），限额约束比原类别更细。")
    if n_merge == 0 and n_split == 0:
        notes.append("聚类结果与原 cat 分组基本一致（无跨类合并、无类别拆散）。")
    notes.append(f"汇总: {n_merge} 个簇发生跨原类别合并, {n_split} 个原类别被拆散到多簇。")
    # 单链效应诚实提示: 并查集是单链合并, 阈值偏低时容易连成一个大簇
    big = max(clusters, key=len)
    if len(big) >= 0.6 * len(label):
        notes.append(f"提示: 阈值 {corr_thresh:.2f} 下单链合并把 {len(big)}/{len(label)} 个板块连成一簇"
                     f"（簇内平均相关已贴近阈值, 属链式效应）, 该簇限额实际约束≈全组合最多 {per_cat_max} 个持仓, "
                     f"分散度显著下降; 想要更细的簇可调高 --corr-thresh（如 0.75/0.80）重新聚类。")
    return notes


# ---------------- 数据 / 回测封装 ----------------
def make_rets(dfs: dict, index) -> pd.DataFrame:
    """与回测引擎一致的板块日收益: closes.pct_change().fillna(0)（pandas 2.x FutureWarning 无害）。"""
    closes = pd.DataFrame({n: d["close"] for n, d in dfs.items()}).reindex(index)
    return closes.pct_change().fillna(0.0)


def metrics_row(name: str, m: dict, overlay: bool) -> dict:
    """单方案指标行（Calmar = CAGR / |MDD|）。"""
    mdd = float(m["max_drawdown"])
    calmar = float(m["cagr"]) / abs(mdd) if mdd < 0 else float("nan")
    return {
        "方案": name,
        "CAGR": float(m["cagr"]),
        "年化波动": float(m["ann_vol"]),
        "Sharpe": float(m["sharpe"]),
        "MDD": mdd,
        "Calmar": calmar,
        "笔数": "权益层无交易统计" if overlay else int(m["n_trades"]),
    }


def format_rows(rows):
    """数值格式化成展示表。"""
    disp = []
    for r in rows:
        disp.append({
            "方案": r["方案"],
            "CAGR": f"{r['CAGR'] * 100:+.1f}%",
            "年化波动": f"{r['年化波动'] * 100:.1f}%",
            "Sharpe": f"{r['Sharpe']:.2f}",
            "MDD": f"{r['MDD'] * 100:.1f}%",
            "Calmar": f"{r['Calmar']:.2f}" if np.isfinite(r["Calmar"]) else "-",
            "笔数": str(r["笔数"]),
        })
    return pd.DataFrame(disp)


# ---------------- Markdown / 绘图 ----------------
def md_table(df: pd.DataFrame) -> str:
    """DataFrame -> markdown 表格（不依赖 tabulate）。"""
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |",
             "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        cells = ["-" if (pd.isna(v)) else str(v) for v in r]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _dd_spans(index, mask):
    """mask=True 的连续区间 [(start, end), ...]，用于 axvspan 阴影。"""
    spans, start = [], None
    for dt, m in zip(index, mask):
        if m and start is None:
            start = dt
        elif not m and start is not None:
            spans.append((start, dt))
            start = None
    if start is not None:
        spans.append((start, index[-1]))
    return spans


def plot_curves(curves: dict, base_dd: pd.Series, dd_soft: float, png_path: str, title: str):
    """权益曲线 + 回撤子图；基线深回撤（<= dd_soft）区间红色阴影。"""
    plt.rcParams["font.sans-serif"] = config.PLOT_FONTS
    plt.rcParams["axes.unicode_minus"] = False
    styles = {
        SCHEME_NAMES[0]: dict(color="#3b3b3b", lw=1.8, ls="-"),
        SCHEME_NAMES[1]: dict(color="#1f77b4", lw=1.4, ls="-"),
        SCHEME_NAMES[4]: dict(color="#ff7f0e", lw=1.4, ls="--"),
        SCHEME_NAMES[5]: dict(color="#2ca02c", lw=1.8, ls="-"),
    }
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True,
                                   gridspec_kw={"height_ratios": [2.2, 1]})
    spans = _dd_spans(base_dd.index, (base_dd <= dd_soft).values)
    shade_label = f"基线回撤>{abs(dd_soft) * 100:.0f}%区间"
    for ax in (ax1, ax2):
        for i, (d0, d1) in enumerate(spans):
            ax.axvspan(d0, d1, color="red", alpha=0.07, lw=0,
                       label=shade_label if i == 0 else None)
    for name, eq in curves.items():
        st = styles.get(name, {})
        ax1.plot(eq.index, eq["equity"].values, label=name, **st)
        dd = eq["equity"] / eq["equity"].cummax() - 1
        ax2.plot(dd.index, dd.values * 100, label=name, **st)
    ax1.axhline(1.0, color="#999999", lw=0.8, ls=":")
    ax1.set_ylabel("组合净值（起点=1）")
    ax1.set_title(title, fontsize=13)
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(alpha=0.25)
    ax2.set_ylabel("回撤（%）")
    ax2.legend(loc="lower left", fontsize=9)
    ax2.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150)
    plt.close(fig)


# ---------------- 结论（用真实数字生成） ----------------
def build_conclusion(rows):
    by = {r["方案"]: r for r in rows}
    base, s1, s2, s3, s4, s5 = (by[n] for n in SCHEME_NAMES)

    def _p(x):
        return f"{x * 100:.1f}%"

    def _pf(x):
        return f"{x * 100:+.1f}%"

    best = max(rows, key=lambda r: (r["Calmar"] if np.isfinite(r["Calmar"]) else -np.inf, r["CAGR"]))
    L = []
    lift = f"提升 {best['Calmar'] / base['Calmar'] - 1:+.0%}" if base["Calmar"] > 0 else "-"
    L.append(f"1. **Calmar 最高: {best['方案']}** —— CAGR {_p(best['CAGR'])}, 年化波动 {_p(best['年化波动'])}, "
             f"MDD {_p(best['MDD'])}, Calmar {best['Calmar']:.2f}"
             f"（基线 Calmar {base['Calmar']:.2f}, {lift}）。")

    d_cagr = best["CAGR"] - base["CAGR"]
    d_mdd = best["MDD"] - base["MDD"]   # >0 为改善
    L.append(f"2. **收益-回撤权衡**: 相比基线（CAGR {_p(base['CAGR'])}, MDD {_p(base['MDD'])}）, "
             f"{best['方案']} CAGR 变化 {d_cagr * 100:+.1f}pct（{_p(base['CAGR'])} → {_p(best['CAGR'])}）, "
             f"MDD 改善 {d_mdd * 100:+.1f}pct（{_p(base['MDD'])} → {_p(best['MDD'])}）。")
    if d_cagr < 0 and d_mdd > 0:
        L.append(f"   即牺牲 {abs(d_cagr) * 100:.1f}pct 年化收益, 换回 {d_mdd * 100:.1f}pct 最大回撤改善, "
                 f"每牺牲 1pct CAGR 换回约 {d_mdd / abs(d_cagr):.1f}pct MDD。")
    else:
        L.append(f"   （{'未牺牲收益' if d_cagr >= 0 else '收益下降'}, "
                 f"{'回撤同时改善' if d_mdd > 0 else '回撤未改善'}。）")

    dd_best = max(rows, key=lambda r: r["MDD"])   # MDD 为负, 最大者=回撤最浅
    L.append(f"3. **回撤控制 vs 波动率目标**: 只开回撤迟滞（方案1）CAGR {_p(s1['CAGR'])} / MDD {_p(s1['MDD'])}; "
             f"只开波动率目标（方案2）CAGR {_p(s2['CAGR'])} / MDD {_p(s2['MDD'])}; "
             f"两者同开（方案3）CAGR {_p(s3['CAGR'])} / MDD {_p(s3['MDD'])}；"
             f"六方案中回撤最浅为 {dd_best['方案']} 的 {_p(dd_best['MDD'])}。")

    L.append(f"4. **相关性聚类限额**: 方案4 CAGR {_p(s4['CAGR'])} / MDD {_p(s4['MDD'])} / "
             f"Calmar {s4['Calmar']:.2f}, 笔数 {s4['笔数']}（基线 {base['笔数']}）; "
             f"再叠回撤控制（方案5）CAGR {_p(s5['CAGR'])} / MDD {_p(s5['MDD'])} / Calmar {s5['Calmar']:.2f}。")

    L.append(f"5. **推荐配置: {best['方案']}**（六方案中 Calmar 最高）。")
    L.append("   *注*: overlay 方案在原回测权重上做权益层缩放, 无独立成交流水（笔数列标注）; "
             "回撤状态机是滞后保护而非前瞻择时; 结论对阈值参数敏感, "
             "可用 --dd-soft / --vol-target / --corr-thresh 复验。")
    return L


# ---------------- 主流程 ----------------
def parse_args():
    ap = argparse.ArgumentParser(description="组合级风控层对比回测（深寒策略 MDD 改善量化）")
    ap.add_argument("--mode", default="cold", help="回测模式（默认 cold 深寒左侧）")
    ap.add_argument("--corr-thresh", type=float, default=0.65, help="相关性聚类阈值（默认 0.65）")
    ap.add_argument("--per-cat-max", type=int, default=2, help="同簇/同类持仓上限（默认 2）")
    ap.add_argument("--dd-soft", type=float, default=-0.15, help="回撤减仓阈值（默认 -0.15）")
    ap.add_argument("--dd-hard", type=float, default=-0.30, help="回撤清仓阈值（默认 -0.30）")
    ap.add_argument("--recover-above", type=float, default=-0.08, help="回撤恢复阈值（默认 -0.08）")
    ap.add_argument("--soft-scale", type=float, default=0.5, help="减仓期权重缩放（默认 0.5）")
    ap.add_argument("--vol-target", type=float, default=0.25, help="年化波动率目标（默认 0.25）")
    ap.add_argument("--vol-window", type=int, default=60, help="已实现波动率窗口（默认 60 日）")
    ap.add_argument("--cost", type=float, default=0.0015, help="单边成本（默认 0.0015）")
    ap.add_argument("--start", default=None, help="回测起始日（默认全样本 2010~末尾）")
    ap.add_argument("--end", default=None, help="回测结束日（默认全样本）")
    return ap.parse_args()


def main():
    args = parse_args()
    obj = pd.read_pickle(PKL)
    dfs, meta = obj["dfs"], obj["meta"]
    data_start = min(d.index[0] for d in dfs.values())
    data_end = max(d.index[-1] for d in dfs.values())

    print("=" * 100)
    print(f"组合级风控层对比回测  mode={args.mode}  板块数={len(dfs)}  "
          f"区间 {data_start.date()} ~ {data_end.date()}")
    print(f"风控参数: corr_thresh={args.corr_thresh} 簇限额={args.per_cat_max} | "
          f"dd_soft={args.dd_soft} scale={args.soft_scale} dd_hard={args.dd_hard} "
          f"recover={args.recover_above} | vol_target={args.vol_target}(win={args.vol_window}) | "
          f"cost={args.cost}")
    print("=" * 100)

    # ---- 方案0: 基线 ----
    b0 = run_backtest(dfs, meta, mode=args.mode, cost_rate=args.cost,
                      start=args.start, end=args.end)
    rets = make_rets(dfs, b0["weights"].index)

    # ---- 相关性聚类（方案4 用）: 全样本日收益相关矩阵 + 并查集 ----
    closes = pd.DataFrame({n: d["close"] for n, d in dfs.items()}).reindex(b0["weights"].index)
    corr, clusters, label = cluster_by_correlation(closes.pct_change(), args.corr_thresh)
    meta2 = {name: dict(m, cat=f"簇{label[name]}") for name, m in meta.items()}  # 保留原 meta 其他键

    cluster_rows = []
    for k, members in enumerate(clusters):
        cats = pd.Series([meta.get(s, {}).get("cat", "?") for s in members]).value_counts()
        cluster_rows.append({
            "簇": f"簇{k}",
            "板块数": len(members),
            "板块": "、".join(members),
            "簇内平均相关": _intra_mean_corr(corr, members),
            "原类别构成": "+".join(f"{c}×{n}" for c, n in cats.items()),
        })
    cluster_disp = pd.DataFrame([{
        "簇": r["簇"], "板块数": r["板块数"], "板块": r["板块"],
        "簇内平均相关": f"{r['簇内平均相关']:.2f}" if np.isfinite(r["簇内平均相关"]) else "-",
        "原类别构成": r["原类别构成"],
    } for r in cluster_rows])
    notes = cluster_diff_notes(meta, clusters, label, args.corr_thresh, args.per_cat_max)

    # ---- 方案4: 聚类限额回测（meta 的 cat 换成相关性簇编号, 引擎不用改） ----
    b4 = run_backtest(dfs, meta2, mode=args.mode, cost_rate=args.cost,
                      per_category_max=args.per_cat_max, start=args.start, end=args.end)
    rets4 = make_rets(dfs, b4["weights"].index)

    # ---- overlay 风控层（方案1/2/3/5） ----
    dd_kw = dict(dd_soft=args.dd_soft, dd_hard=args.dd_hard,
                 recover_above=args.recover_above, soft_scale=args.soft_scale)
    o1 = overlay_equity(b0["weights"], rets, cost_rate=args.cost, vol_target=None, **dd_kw)
    o2 = overlay_equity(b0["weights"], rets, cost_rate=args.cost, vol_target=args.vol_target,
                        vol_window=args.vol_window, dd_soft=DD_OFF, dd_hard=DD_OFF,
                        recover_above=args.recover_above, soft_scale=args.soft_scale)
    o3 = overlay_equity(b0["weights"], rets, cost_rate=args.cost, vol_target=args.vol_target,
                        vol_window=args.vol_window, **dd_kw)
    o5 = overlay_equity(b4["weights"], rets4, cost_rate=args.cost, vol_target=None, **dd_kw)

    # ---- 六方案对比表 ----
    rows = [
        metrics_row(SCHEME_NAMES[0], b0["metrics"], overlay=False),
        metrics_row(SCHEME_NAMES[1], o1["metrics"], overlay=True),
        metrics_row(SCHEME_NAMES[2], o2["metrics"], overlay=True),
        metrics_row(SCHEME_NAMES[3], o3["metrics"], overlay=True),
        metrics_row(SCHEME_NAMES[4], b4["metrics"], overlay=False),
        metrics_row(SCHEME_NAMES[5], o5["metrics"], overlay=True),
    ]
    disp = format_rows(rows)
    conclusion = build_conclusion(rows)

    # ---- 终端输出 ----
    print("\n--- 相关性聚类结果（并查集, corr > %.2f 合并同簇）---" % args.corr_thresh)
    print(cluster_disp.to_string(index=False))
    print("\n--- 与原 cat 分组的差异 ---")
    for n in notes:
        print("  * " + n)
    print("\n--- 六方案对比（全样本 %s ~ %s, mode=%s）---"
          % (b0["weights"].index[0].date(), b0["weights"].index[-1].date(), args.mode))
    print(disp.to_string(index=False))
    print("\n--- 结论 ---")
    for line in conclusion:
        print("  " + line.replace("**", ""))

    # ---- PNG: 基线 + 方案1 + 方案4/5 权益曲线（回撤区间阴影） ----
    curves = {
        SCHEME_NAMES[0]: b0["equity"],
        SCHEME_NAMES[1]: o1["equity"],
        SCHEME_NAMES[4]: b4["equity"],
        SCHEME_NAMES[5]: o5["equity"],
    }
    base_dd = b0["equity"]["equity"] / b0["equity"]["equity"].cummax() - 1
    title = f"深寒策略组合级风控层对比（mode={args.mode}, {b0['weights'].index[0].date()} ~ {b0['weights'].index[-1].date()}）"
    plot_curves(curves, base_dd, args.dd_soft, PNG_PATH, title)

    # ---- Markdown 报告 ----
    md = []
    md.append("# 组合级风控层对比回测 —— 深寒策略 MDD 改善量化\n")
    md.append(f"- 生成时间: {datetime.now():%Y-%m-%d %H:%M}")
    md.append(f"- 数据: `data/enriched.pkl`（{len(dfs)} 个板块, 数据 {data_start.date()} ~ {data_end.date()}）")
    md.append(f"- 策略: mode={args.mode}（温度<12 买入 / >75 卖出）, 单边成本 {args.cost:.2%}, "
              f"组合上限 {config.STRATEGY['cold_max_positions']} 仓, 移动止损 {config.STRATEGY['trailing_stop'][args.mode]:.0%}")
    md.append(f"- 风控参数: 回撤迟滞 soft={args.dd_soft:.0%} / scale={args.soft_scale} / hard={args.dd_hard:.0%} / "
              f"recover={args.recover_above:.0%}; 波动率目标 {args.vol_target:.0%}（{args.vol_window} 日窗口）; "
              f"相关性阈值 {args.corr_thresh} + 并查集聚簇, 簇限额 {args.per_cat_max}")
    md.append("- 说明: 方案2 以 dd_soft=dd_hard=-100% 关闭回撤状态机（overlay_equity 内回撤控制无独立开关）, "
              "只保留波动率目标; 方案3 为回撤迟滞默认参数 + 波动率目标同开。"
              "overlay 方案为权益层权重缩放, 无成交流水。\n")

    md.append("## 一、相关性聚类结果（并查集, corr > %.2f 合并同簇）\n" % args.corr_thresh)
    md.append(md_table(cluster_disp) + "\n")
    md.append("**与原 cat 分组的差异**:\n")
    for n in notes:
        md.append(f"- {n}")
    md.append("")

    md.append("## 二、六方案对比\n")
    md.append(md_table(disp) + "\n")
    md.append("（Calmar = CAGR ÷ |MDD|; 笔数 = 回测引擎成交流水数, overlay 方案为权益层权重缩放, 无交易统计）\n")

    md.append("## 三、结论\n")
    for line in conclusion:
        md.append(line)
    md.append("")

    with open(MD_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"\n[输出] {MD_PATH}")
    print(f"[输出] {PNG_PATH}")


if __name__ == "__main__":
    main()
