# -*- coding: utf-8 -*-
"""
报告生成：图表（matplotlib 中文）+ Markdown 报告。
"""
import os

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

import config  # noqa: E402
from .cycle_engine import STAGE_CN  # noqa: E402

plt.rcParams["font.sans-serif"] = config.PLOT_FONTS
plt.rcParams["axes.unicode_minus"] = False

OUT = config.OUTPUT_DIR
os.makedirs(OUT, exist_ok=True)


def _md_table(df: pd.DataFrame, floatfmt="{:.3f}", pct_cols=(), int_cols=()):
    """DataFrame -> markdown 表格（不依赖 tabulate）。"""
    if df is None or len(df) == 0:
        return "*(无数据)*\n"
    df = df.copy()

    def fmt(col, v):
        if isinstance(v, str):
            return v
        try:
            if pd.isna(v):
                return "-"
        except Exception:
            pass
        if col in pct_cols:
            return f"{v * 100:.1f}%"
        if col in int_cols:
            return f"{int(v):,}"
        if isinstance(v, float):
            return floatfmt.format(v)
        return str(v)

    headers = list(df.columns)
    lines = ["| " + " | ".join(str(h) for h in headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(fmt(c, row[c]) for c in headers) + " |")
    return "\n".join(lines) + "\n"


# ---------------- 图表 ----------------

def plot_sector_overview(df: pd.DataFrame, name: str, fname: str):
    """板块总览: 价格+阶段着色 / 周期温度 / PB分位。"""
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1.5, 1.2]})
    ax1, ax2, ax3 = axes

    # 阶段着色背景
    stage = df["stage"]
    for st, color in config.STAGE_COLORS.items():
        mask = (stage == st)
        if not mask.any():
            continue
        # 连续区间
        chg = (mask != mask.shift(1)).cumsum()
        for _, g in mask.groupby(chg):
            if not g.iloc[0]:
                continue
            ax1.axvspan(g.index[0], g.index[-1], color=color, alpha=0.18, lw=0)

    ax1.plot(df.index, df["close"], color="#222", lw=0.9, label="行业指数")
    if "ma200" in df.columns:
        ax1.plot(df.index, df["ma200"], color="#e74c3c", lw=0.8, alpha=0.8, label="MA200")
    ax1.set_title(f"{name} — 周期阶段与温度", fontsize=14)
    ax1.legend(loc="upper left", fontsize=9)
    ax1.set_ylabel("指数点位")

    ax2.plot(df.index, df["temp"], color="#2980b9", lw=0.9)
    for y, c, lb in [(20, "#3498db", "冰点"), (40, "#f39c12", "启动线"),
                     (55, "#e67e22", "扩张线"), (80, "#c0392b", "过热线")]:
        ax2.axhline(y, color=c, ls="--", lw=0.7, alpha=0.7)
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("周期温度")

    if "pb_pct" in df.columns and df["pb_pct"].notna().any():
        ax3.plot(df.index, df["pb_pct"], color="#8e44ad", lw=0.9)
        ax3.axhline(0.85, color="#c0392b", ls="--", lw=0.7)
        ax3.axhline(0.10, color="#27ae60", ls="--", lw=0.7)
        ax3.set_ylabel("PB分位")
    else:
        ax3.text(0.5, 0.5, "无PB数据", transform=ax3.transAxes, ha="center")

    legend_items = [Patch(facecolor=c, alpha=0.35, label=STAGE_CN[s])
                    for s, c in config.STAGE_COLORS.items()]
    ax1.legend(handles=legend_items + ax1.get_legend_handles_labels()[0],
               loc="upper left", fontsize=8, ncol=3)

    fig.tight_layout()
    path = os.path.join(OUT, fname)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def plot_stage_stats(pooled: pd.DataFrame, fname="stage_stats.png"):
    """核心图: 各阶段买入的胜率与平均收益（跨板块汇总）。"""
    if pooled is None or len(pooled) == 0:
        return None
    horizons = sorted(pooled["horizon"].unique())
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    stages = ["bottom", "recovery", "expansion", "topping", "decline"]
    stage_labels = [STAGE_CN[s] for s in stages]
    x = np.arange(len(stages))
    width = 0.8 / len(horizons)

    for i, h in enumerate(horizons):
        sub = pooled[(pooled["horizon"] == h)].set_index("stage").reindex(stages)
        axes[0].bar(x + i * width - 0.4 + width / 2, sub["avg_win_rate"], width,
                    label=f"持有{h}日")
        axes[1].bar(x + i * width - 0.4 + width / 2, sub["avg_mean_ret"], width,
                    label=f"持有{h}日")

    axes[0].set_title("各周期阶段买入的胜率（跨板块平均）", fontsize=13)
    axes[0].set_ylabel("胜率")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(stage_labels)
    axes[0].axhline(0.5, color="gray", ls="--", lw=0.8)
    axes[0].legend(fontsize=9)

    axes[1].set_title("各周期阶段买入的平均收益（跨板块平均）", fontsize=13)
    axes[1].set_ylabel("平均收益")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(stage_labels)
    axes[1].axhline(0, color="gray", ls="--", lw=0.8)
    axes[1].legend(fontsize=9)

    fig.tight_layout()
    path = os.path.join(OUT, fname)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def plot_temp_buckets(bucket_df: pd.DataFrame, fname="temp_buckets.png"):
    """温度分桶: 胜率与平均收益随温度的变化。"""
    if bucket_df is None or len(bucket_df) == 0:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for h, sub in bucket_df.groupby("horizon"):
        sub = sub.sort_values("stage_num")
        axes[0].plot(sub["stage_num"] + 5, sub["win_rate"], marker="o", ms=4, label=f"持有{h}日")
        axes[1].plot(sub["stage_num"] + 5, sub["mean_ret"], marker="o", ms=4, label=f"持有{h}日")
    axes[0].set_xlabel("周期温度")
    axes[0].set_ylabel("胜率")
    axes[0].set_title("买入胜率 vs 周期温度")
    axes[0].axhline(0.5, color="gray", ls="--", lw=0.8)
    axes[0].legend(fontsize=9)
    axes[1].set_xlabel("周期温度")
    axes[1].set_ylabel("平均收益")
    axes[1].set_title("平均收益 vs 周期温度")
    axes[1].axhline(0, color="gray", ls="--", lw=0.8)
    axes[1].legend(fontsize=9)
    fig.tight_layout()
    path = os.path.join(OUT, fname)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def plot_equity(curves: dict, fname="backtest_equity.png"):
    """回测净值曲线对比。curves: {名称: equity DataFrame}"""
    fig, ax = plt.subplots(figsize=(13, 6))
    for name, eq in curves.items():
        if eq is None or len(eq) == 0:
            continue
        ax.plot(eq.index, eq["equity"], lw=1.2, label=name)
    ax.set_title("周期轮动策略回测净值（对数坐标）", fontsize=13)
    ax.set_yscale("log")
    ax.legend(fontsize=10)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = os.path.join(OUT, fname)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def plot_current_heatmap(snapshots: pd.DataFrame, fname="current_status.png"):
    """当前各板块周期温度横向条形图（按温度排序，阶段着色）。"""
    if snapshots is None or len(snapshots) == 0:
        return None
    df = snapshots.sort_values("temp")
    fig, ax = plt.subplots(figsize=(10, 0.42 * len(df) + 1.5))
    colors = [config.STAGE_COLORS.get(s, "#999") for s in df["stage"]]
    bars = ax.barh(df["sector"], df["temp"], color=colors, alpha=0.85)
    for bar, t in zip(bars, df["temp"]):
        ax.text(min(t + 1, 97), bar.get_y() + bar.get_height() / 2,
                f"{t:.0f}", va="center", fontsize=9)
    ax.set_xlim(0, 100)
    ax.set_xlabel("周期温度 (0=深度冰点, 100=过热见顶)")
    ax.set_title("当前各周期板块温度计", fontsize=13)
    legend_items = [Patch(facecolor=c, label=STAGE_CN[s]) for s, c in config.STAGE_COLORS.items()]
    ax.legend(handles=legend_items, fontsize=8, loc="lower right")
    fig.tight_layout()
    path = os.path.join(OUT, fname)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def plot_trade_scatter(trades: pd.DataFrame, fname="trades.png"):
    """回测交易散点: 每笔收益 vs 持有天数。"""
    if trades is None or len(trades) == 0:
        return None
    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ["#27ae60" if r > 0 else "#c0392b" for r in trades["ret"]]
    ax.scatter(trades["days"], trades["ret"] * 100, c=colors, alpha=0.65, s=42)
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_xlabel("持有天数")
    ax.set_ylabel("单笔收益 %")
    ax.set_title(f"回测交易分布（{len(trades)} 笔，胜率 {(trades['ret'] > 0).mean() * 100:.0f}%）")
    fig.tight_layout()
    path = os.path.join(OUT, fname)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path
