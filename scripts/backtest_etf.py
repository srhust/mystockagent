# -*- coding: utf-8 -*-
"""
ETF 载体回测：把"信号用行业指数、成交用可交易 ETF"的实盘载体回测做出来。

背景
----
周期系统的信号（mode='cold': 温度<12 买入、>75 卖出）在行业指数上计算,
但指数本身不可交易。本脚本把同样的信号落到真实可交易的行业 ETF/LOF 上
（config.ETF_MAP, 26 个板块）, 并加入 A 股涨停/停牌执行约束,
度量"指数信号 → 可交易载体"的损耗:
  - 跟踪拖累   = A.CAGR − B.CAGR（指数版 − ETF版）
  - 执行约束影响 = B2.CAGR − B.CAGR（无涨停版 − 带涨停版）

时序与组合约定（逐行镜像 cycle_system/backtest.py::run_backtest 的代码行为）
----
- 逐日循环: 先"昨日权重 × 今日收益"结算当日损益, 再按当日目标仓位收盘调仓
  （即: T 日收盘用指数数据算信号、T 日收盘价成交, 仓位自 T+1 起承担收益——
  与引擎代码一致, 保证 A−B 只反映载体/执行差异, 不混入时点差异）;
- 组合 max_positions 等权（每仓 1/max_positions）, 卖出优先、空位当日可回补;
- 买入候选按 strategy.signal_strength（指数侧动量+相对强度+温度斜率）降序;
- 同一周期类别 per_category_max 上限; 未满仓资金视为现金, 不再平衡;
- 成本: 当日换手率 × cost_rate（单边）, 逐日计提。

执行约束（本脚本新增, 指数版没有）
----
- 涨停→买入顺延: 成交日 ETF 当日涨幅 = close/prev_close−1 ≥ +9.5% 时买不进,
  顺延到下一交易日连续尝试; 连续顺延超过 10 个交易日仍无法成交 → 放弃该次买入,
  且该板块在其信号归零前不再买入;
- 跌停→卖出顺延: 成交日跌幅 ≤ −9.5% 时卖不出, 留仓次日继续尝试
  （跌停卖不出, 能卖的第一天卖出）;
- 停牌: ETF 当日无 K 线 → 用最近可用价 ffill 估值, 买卖均顺延到下一个有 K 线的日子;
- --no-limit 关闭涨跌停顺延做对照（停牌顺延保留——没有 K 线物理上无法成交）。

对比组（同窗口同参数）
----
A   指数版 cold（直接调 run_backtest, 参与板块; 指数无涨停）——主对照组
A+  指数版 cold 全部 30 板块（参考, 含无 ETF 的造纸/航运港口/航空机场）
B   ETF 版 cold + 涨停/停牌约束
B2  ETF 版 cold 无涨跌停约束（--no-limit 时作为主 ETF 版, 不重复输出）
C   等权持有全部参与 ETF（逐日等权、零成本, 简单实现的被动基准）
D   沪深300 买入持有

用法
----
python scripts/backtest_etf.py                      # 默认 2021-07-01 ~ 数据末尾
python scripts/backtest_etf.py --window 2019-01-01  # 调整窗口起点
python scripts/backtest_etf.py --enter 12 --exit 75 --cost 0.0015
python scripts/backtest_etf.py --no-limit           # B 用无涨停口径
python scripts/backtest_etf.py --offline            # 只用本地缓存

输出
----
- 终端: 对比组指标表 + 每板块跟踪质量表
- output/etf_backtest.md : 全部结果 + 结论
- output/etf_curve.png   : A/B/B2/C/D 权益曲线
- output/etf_tracking.csv: 板块跟踪质量表
"""
import argparse
import os
import shutil
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401  (加入 .libs)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import data  # noqa: E402  (NO_PROXY/超时兜底; 先于任何 akshare 使用)
from cycle_system import backtest as btengine  # noqa: E402
from cycle_system import strategy as strat  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.sans-serif"] = config.PLOT_FONTS   # 中文字体, 同 cycle_system/report.py
plt.rcParams["axes.unicode_minus"] = False

warnings.filterwarnings("ignore", category=FutureWarning)  # pandas 2.x pct_change 提示, 无害

PKL = os.path.join(config.DATA_DIR, "enriched.pkl")
OUT_DIR = config.OUTPUT_DIR
DEF_START = "2021-07-01"
LIMIT_TH = 0.095    # 涨/跌停判定阈值: 当日涨跌幅 ≥ +9.5% / ≤ −9.5%（10% 限制的近似口径）
MAX_DEFER = 10      # 买入连续顺延上限（交易日）, 超过则放弃该次买入

# 结构性错配标注（载体与板块基本面错位）
CARRIER_TAG = {
    "贵金属": "结构性错配(金价ETF vs 金矿股指数)",
    "油气开采": "结构性错配(QDII LOF, 油价+溢价)",
    "石油加工": "近似载体(原油LOF vs 炼化股)",
}


# ---------------------------------------------------------------- 数据加载

def load_signals():
    """读取富化信号缓存（enriched.pkl: {dfs, meta, ts}）。"""
    if not os.path.exists(PKL):
        print(f"错误: 未找到 {PKL}, 请先运行 python main.py 生成富化数据")
        sys.exit(1)
    obj = pd.read_pickle(PKL)
    dfs, meta = obj["dfs"], obj["meta"]
    last = max(df.index[-1] for df in dfs.values())
    print(f"[data] 富化信号: {len(dfs)} 板块 | 数据截止 {last.date()}")
    return dfs, meta


def load_etfs(sectors):
    """逐板块拉取 ETF 日线（data.get_etf_hist, 失败 warn 跳过该板块）。

    复权: get_etf_hist 默认 adjusted=True, 内部经 data.adjust_etf_prices 做
    份额折算/合并修复（新浪源不复权, 2021-2026 universe 内 6 起单日 ±50~170% 伪影,
    不修复会把折算当涨跌、拆分当腰斩）+ 分红除息近似全收益复权, 本脚本直接采用。

    快照缓存 output/etf_cache/: 首次从共享缓存 data/etf_*.csv / etfdiv_*.csv 导入
    （无共享缓存则联网拉取）后冻结, 隔离其他进程对共享缓存的并发重写——
    本任务执行期间共享缓存曾被其他进程反复重写, 同日两次运行结果不同;
    快照 + --offline 保证本脚本结果可复现。每交易日删除快照目录即可换新数据。

    Returns {板块: 复权后 close Series(ETF 自身日期索引)}。
    """
    shared_cache = data.CACHE
    data.CACHE = os.path.join(OUT_DIR, "etf_cache")   # 私有快照目录
    os.makedirs(data.CACHE, exist_ok=True)
    out = {}
    try:
        for i, s in enumerate(sectors, 1):
            sym, short, _note = config.ETF_MAP[s]
            try:
                for prefix in ("etf_", "etfdiv_"):
                    snap = os.path.join(data.CACHE, f"{prefix}{sym}.csv")
                    if not os.path.exists(snap):
                        shared = os.path.join(shared_cache, f"{prefix}{sym}.csv")
                        if os.path.exists(shared):
                            shutil.copyfile(shared, snap)
            except Exception as e:  # noqa: BLE001
                print(f"[etf] {sym} 快照导入失败({type(e).__name__}), 回退共享缓存/联网")
            try:
                df = data.get_etf_hist(sym)
            except Exception as e:  # noqa: BLE001
                print(f"[etf] ({i}/{len(sectors)}) {s} {sym} 拉取异常({type(e).__name__}), 跳过该板块")
                continue
            if df is None or len(df) == 0 or "close" not in df.columns:
                print(f"[etf] ({i}/{len(sectors)}) {s} {sym} 无数据, 跳过该板块")
                continue
            ser = df.set_index("date")["close"].astype(float)
            ser = ser[~ser.index.duplicated(keep="last")].sort_index()
            out[s] = ser
            print(f"[etf] ({i}/{len(sectors)}) {s:<6} {sym} {short}: {len(df)}行 "
                  f"{df['date'].min().date()} ~ {df['date'].max().date()}（折算+分红复权后）")
    finally:
        data.CACHE = shared_cache
    return out


# ---------------------------------------------------------------- ETF 版逐日模拟

def simulate_etf(idx_dfs, cat_meta, etf_close, cal, act_mask,
                 enter_below, exit_above, max_positions, per_cat, cost_rate,
                 apply_limit=True, label="ETF版"):
    """ETF 载体逐日模拟（镜像 run_backtest 的组合规则 + 涨停/停牌执行约束）。

    Parameters
    ----------
    idx_dfs : {板块: 富化指数df}（信号源: temp/stage/mom60/rs60）
    cat_meta : {板块: {"cat": 周期类别}}
    etf_close : {板块: ETF 收盘价 Series（真实 K 线, 自身日期索引）}
    cal : 交易日历（指数日历 ∩ ETF 日期并集, 已按窗口过滤）
    act_mask : {板块: bool Series(cal)}  ETF 上市后才参与
    apply_limit : 是否启用涨跌停顺延（停牌顺延始终启用）

    Returns
    -------
    dict(equity, trades, weights, metrics, defer={buy, sell, abandon})
    """
    sectors = [s for s in etf_close if s in idx_dfs]
    n, k = len(cal), len(sectors)
    t0 = time.time()

    tgt = np.zeros((n, k), dtype=np.int8)        # 目标仓位（指数信号, 引擎同源）
    strength = np.zeros((n, k))                  # 候选排序（指数侧信号强度）
    px = np.full((n, k), np.nan)                 # ETF 收盘（停牌 ffill, 上市前 NaN）
    has_bar = np.zeros((n, k), dtype=bool)       # 当日是否有真实 K 线
    up_lim = np.zeros((n, k), dtype=bool)        # 当日涨幅 ≥ +9.5%（涨停, 买不进）
    dn_lim = np.zeros((n, k), dtype=bool)        # 当日跌幅 ≤ −9.5%（跌停, 卖不出）
    act = np.zeros((n, k), dtype=bool)           # ETF 已上市参与

    for j, s in enumerate(sectors):
        df = idx_dfs[s]
        pos = strat.position_target(df, mode="cold",
                                    enter_below=enter_below, exit_above=exit_above)
        tgt[:, j] = pos.reindex(cal).fillna(0).astype(int).to_numpy()
        strength[:, j] = strat.signal_strength(df).reindex(cal).fillna(0.0).to_numpy()
        ec = etf_close[s]
        bar_ret = ec.pct_change()                # 真实 K 线逐日涨幅（涨停判定用）
        c = ec.reindex(cal)
        has_bar[:, j] = c.notna().to_numpy()
        px[:, j] = c.ffill().to_numpy()
        if apply_limit:
            up_lim[:, j] = (bar_ret >= LIMIT_TH).reindex(cal).fillna(False).to_numpy(dtype=bool)
            dn_lim[:, j] = (bar_ret <= -LIMIT_TH).reindex(cal).fillna(False).to_numpy(dtype=bool)
        act[:, j] = act_mask[s].to_numpy()

    # 组合日收益: ffill 价格 pct_change（停牌日 0, 复牌日补足全程涨幅）
    rets = pd.DataFrame(px, index=cal, columns=sectors) \
        .pct_change(fill_method=None).fillna(0.0).to_numpy()

    w = np.zeros(k)
    equity = 1.0
    eq_curve = np.zeros(n)
    w_hist = np.zeros((n, k))
    trades = []
    entry = {}                                   # j -> (entry_price, entry_date)
    buy_def = np.zeros(k, dtype=int)             # 当次买入尝试的连续顺延天数
    buy_def_total = np.zeros(k, dtype=int)
    sell_def_total = np.zeros(k, dtype=int)
    buy_abandon = np.zeros(k, dtype=int)
    blocked = np.zeros(k, dtype=bool)            # 放弃买入后, 信号归零前禁止再买

    for i in range(n):
        d = cal[i]
        # ---- 当日损益: 昨日权重 × 今日收益（镜像引擎） ----
        if i > 0:
            equity *= (1.0 + float((w * rets[i]).sum()))

        new_w = w.copy()

        # ---- 卖出（目标 0; 跌停/停牌卖不出 → 留仓次日再试） ----
        for j in range(k):
            if w[j] > 0 and tgt[i, j] == 0:
                if has_bar[i, j] and (not apply_limit or not dn_lim[i, j]):
                    ep, ed = entry.pop(j)
                    px_now = float(px[i, j])
                    trades.append({
                        "sector": sectors[j], "entry_date": ed, "exit_date": d,
                        "entry_price": ep, "exit_price": px_now,
                        "ret": px_now / ep - 1.0,
                        "days": (d - ed).days,
                        "exit_reason": "signal",
                    })
                    new_w[j] = 0.0
                else:
                    sell_def_total[j] += 1

        # ---- 买入（目标 1; 涨停/停牌买不进 → 顺延, 超 10 日放弃） ----
        held = [j for j in range(k) if new_w[j] > 0]
        slots = max_positions - len(held)
        if slots > 0:
            cats = {}
            for j in held:
                c = cat_meta[sectors[j]]["cat"]
                cats[c] = cats.get(c, 0) + 1
            cands = [j for j in range(k)
                     if new_w[j] == 0 and tgt[i, j] == 1 and act[i, j]
                     and np.isfinite(px[i, j]) and not blocked[j]]
            cands.sort(key=lambda j: strength[i, j], reverse=True)
            for j in cands:
                if slots <= 0:
                    break
                c = cat_meta[sectors[j]]["cat"]
                if cats.get(c, 0) >= per_cat:
                    continue
                if has_bar[i, j] and (not apply_limit or not up_lim[i, j]):
                    new_w[j] = 1.0 / max_positions
                    entry[j] = (float(px[i, j]), d)
                    buy_def[j] = 0
                    cats[c] = cats.get(c, 0) + 1
                    slots -= 1
                else:
                    # 涨停/停牌 → 顺延; 连续超过 MAX_DEFER 个交易日 → 放弃该次买入
                    buy_def[j] += 1
                    buy_def_total[j] += 1
                    if buy_def[j] > MAX_DEFER:
                        blocked[j] = True
                        buy_abandon[j] += 1
                        buy_def[j] = 0

        # ---- 信号归零日: 复位顺延计数与放弃封锁 ----
        for j in range(k):
            if tgt[i, j] == 0:
                buy_def[j] = 0
                blocked[j] = False

        # ---- 成本: 当日换手率 × 单边成本（镜像引擎） ----
        turnover = float(np.abs(new_w - w).sum())
        equity *= (1.0 - turnover * cost_rate)
        eq_curve[i] = equity
        w_hist[i] = new_w
        w = new_w

    equity_df = pd.DataFrame({"equity": eq_curve}, index=cal)
    trades_df = pd.DataFrame(trades)
    metrics = btengine.compute_metrics(equity_df, trades_df if len(trades_df) else None, None)
    defer = {
        "buy": pd.Series(buy_def_total, index=sectors),
        "sell": pd.Series(sell_def_total, index=sectors),
        "abandon": pd.Series(buy_abandon, index=sectors),
    }
    lim_txt = "涨停+停牌约束" if apply_limit else "仅停牌约束"
    print(f"[bt] {label} ({lim_txt}): {len(sectors)}板块 | 交易{len(trades_df)}笔 | "
          f"年化{metrics['cagr'] * 100:.1f}% | 回撤{metrics['max_drawdown'] * 100:.1f}% | "
          f"顺延(买{int(defer['buy'].sum())}/卖{int(defer['sell'].sum())}) "
          f"放弃买{int(defer['abandon'].sum())} | {time.time() - t0:.1f}s")
    return {"equity": equity_df, "trades": trades_df, "weights": pd.DataFrame(w_hist, index=cal, columns=sectors),
            "metrics": metrics, "defer": defer}


# ---------------------------------------------------------------- 跟踪质量

def _ann_ret(close_ser):
    """年化收益（自然日复利）。输入 ffilled 收盘价序列。"""
    v = close_ser.dropna()
    if len(v) < 30:
        return np.nan
    yrs = (v.index[-1] - v.index[0]).days / 365.25
    if yrs <= 0.05:
        return np.nan
    return float((v.iloc[-1] / v.iloc[0]) ** (1.0 / yrs) - 1.0)


def build_tracking(idx_dfs, etf_close, cal, act_mask, parts, defer):
    """每板块跟踪质量表: 指数 vs ETF 日收益相关性/年化差/beta + 顺延统计 + 载体标注。

    相关性/beta 只在"指数与 ETF 同日都有真实 K 线"的交易日上计算——
    同花顺指数源存在缺 K 线（如煤炭 2023 年全年缺失 242 日）, 若 ffill 成零收益
    会污染相关统计; 另给"剔极端日(|日收益|>4%)"的稳健相关做交叉验证。
    年化收益用 ffill 端点值计算（缺口不影响端点）。
    """
    sym_counts = pd.Series([config.ETF_MAP[s][0] for s in parts]).value_counts()
    rows = []
    for s in parts:
        sym, short, note = config.ETF_MAP[s]
        ic_raw = idx_dfs[s]["close"].reindex(cal)
        ec_raw = etf_close[s].reindex(cal)
        m = act_mask[s].to_numpy()
        ir = ic_raw[m].pct_change(fill_method=None)
        er = ec_raw[m].pct_change(fill_method=None)
        pair = pd.concat([ir.rename("idx"), er.rename("etf")], axis=1).dropna()
        if len(pair) > 30 and pair["idx"].var() > 0:
            corr = float(pair["idx"].corr(pair["etf"]))
            beta = float(pair["idx"].cov(pair["etf"]) / pair["idx"].var())
        else:
            corr, beta = np.nan, np.nan
        trim = pair[(pair["idx"].abs() <= 0.04) & (pair["etf"].abs() <= 0.04)]
        corr_trim = float(trim["idx"].corr(trim["etf"])) if len(trim) > 30 else np.nan
        n_idx_gap = int(pd.isna(ic_raw[m]).sum())
        ann_i, ann_e = _ann_ret(ic_raw.ffill()[m]), _ann_ret(ec_raw.ffill()[m])
        eff = cal[m][0]
        shared = "共用" if sym_counts[sym] > 1 else ""
        tag = CARRIER_TAG.get(s, "") or ("近似载体(共用)" if shared else "")
        if note:
            tag = f"{tag}; {note}" if tag else note
        rows.append({
            "板块": s,
            "ETF代码": sym,
            "ETF简称": short,
            "ETF起始日": str(etf_close[s].index[0].date()),
            "参与起始日": str(eff.date()),
            "日收益相关": round(corr, 3) if np.isfinite(corr) else np.nan,
            "相关(剔极端日)": round(corr_trim, 3) if np.isfinite(corr_trim) else np.nan,
            "指数缺K线": n_idx_gap,
            "beta(ETF/指数)": round(beta, 2) if np.isfinite(beta) else np.nan,
            "指数年化": _pct(ann_i),
            "ETF年化": _pct(ann_e),
            "年化差(指-ETF)": _pct(ann_i - ann_e) if np.isfinite(ann_i) and np.isfinite(ann_e) else "-",
            "买入顺延": int(defer["buy"].get(s, 0)),
            "卖出顺延": int(defer["sell"].get(s, 0)),
            "买入放弃": int(defer["abandon"].get(s, 0)),
            "共用": shared,
            "载体标注/备注": tag,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 输出工具

def _pct(x):
    return "-" if x is None or not np.isfinite(x) else f"{x * 100:.1f}%"


def _md_table(df):
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if v is None else str(v) for v in r.tolist()) + " |")
    return "\n".join(lines)


def _metrics_row(tag, desc, m):
    return {
        "组别": tag, "说明": desc,
        "CAGR": _pct(m.get("cagr")),
        "年化波动": _pct(m.get("ann_vol")),
        "Sharpe": f"{m.get('sharpe', np.nan):.2f}",
        "最大回撤": _pct(m.get("max_drawdown")),
        "笔数": int(m.get("n_trades", 0) or 0),
        "胜率": _pct(m.get("win_rate")),
    }


def _sanity_curve(tag, eq):
    """权益曲线健全性: 无 NaN 断裂、无非正值。"""
    bad_nan = int(pd.isna(eq).sum())
    bad_neg = int((eq <= 0).sum())
    if bad_nan or bad_neg:
        print(f"[check] 警告: {tag} 权益曲线异常 (NaN={bad_nan}, 非正值={bad_neg})")
        return False
    return True


def plot_curves(curves, path, logy=False):
    """A/B/B2/C/D 权益曲线。curves = [(label, Series), ...]（已对齐同一日历）。"""
    styles = {
        "A": dict(color="#555555", ls="--", lw=1.6),
        "B2": dict(color="#e67e22", ls="-.", lw=1.4),
        "B": dict(color="#d62728", ls="-", lw=1.9),
        "C": dict(color="#1f77b4", ls=":", lw=1.4),
        "D": dict(color="#2ca02c", ls="-", lw=1.4),
    }
    fig, ax = plt.subplots(figsize=(12, 6.5))
    for label, s in curves:
        st = styles.get(label.split(" ")[0], {})
        ax.plot(s.index, s.values, label=label, **st)
    ax.axhline(1.0, color="#bbbbbb", lw=0.8)
    ax.set_title("ETF 载体回测：指数信号（cold 温度<12买/>75卖）落到可交易ETF")
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", fontsize=9)
    if logy:
        ax.set_yscale("log")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(
        description="ETF 载体回测: 指数信号(cold)落到可交易ETF, 含涨停/停牌约束",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--window", default=DEF_START, help="回测起点(YYYY-MM-DD 或 YYYYMMDD)")
    ap.add_argument("--end", default=None, help="回测终点(默认=数据末尾)")
    ap.add_argument("--enter", type=float, default=config.STRATEGY["buy"]["cold"]["enter_below"],
                    help="cold 买入温度阈值")
    ap.add_argument("--exit", type=float, default=config.STRATEGY["sell"]["cold_exit_above"],
                    help="cold 卖出温度阈值")
    ap.add_argument("--cost", type=float, default=0.0015, help="单边成本")
    ap.add_argument("--max-positions", type=int, default=config.STRATEGY["cold_max_positions"],
                    help="最大持仓数(等权)")
    ap.add_argument("--per-cat", type=int, default=config.STRATEGY["per_category_max"],
                    help="同一周期类别持仓上限")
    ap.add_argument("--no-limit", action="store_true",
                    help="B 组关闭涨跌停顺延(B2 口径; 停牌顺延保留)")
    ap.add_argument("--log", action="store_true", help="净值曲线用对数轴")
    ap.add_argument("--offline", action="store_true", help="只用本地缓存, 不联网")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if args.offline:
        data.OFFLINE = True

    t0 = time.time()
    dfs_all, meta_all = load_signals()
    win_start = pd.Timestamp(args.window)
    win_end = pd.Timestamp(args.end) if args.end else None

    # ---- 参与板块 = ETF_MAP ∩ 信号数据 ∩ ETF 数据可用 ----
    parts = [s for s in config.ETF_MAP if s in dfs_all]
    no_sig = [s for s in config.ETF_MAP if s not in dfs_all]
    if no_sig:
        print(f"[data] ETF_MAP 中无信号数据的板块(跳过): {', '.join(no_sig)}")
    etf_close = load_etfs(parts)
    parts = [s for s in parts if s in etf_close]
    first_bar = {s: etf_close[s].index[0] for s in parts}
    drop = [s for s in parts if etf_close[s].index[-1] < win_start]          # 窗口前退市
    if drop:
        print(f"[etf] 窗口前已无K线的板块(跳过): {', '.join(drop)}")
        for s in drop:
            etf_close.pop(s, None)
        parts = [s for s in parts if s not in drop]
    if len(parts) < 2:
        sys.exit("有效参与板块不足, 无法回测")

    # ---- 日历 = 指数日历 ∩ 全体参与 ETF 日期并集, 再按窗口过滤 ----
    idx_dates = set().union(*[set(dfs_all[s].index) for s in parts])
    etf_dates = set().union(*[set(etf_close[s].index) for s in parts])
    cal = pd.DatetimeIndex(sorted(idx_dates & etf_dates))
    cal = cal[cal >= win_start]
    if win_end is not None:
        cal = cal[cal <= win_end]
    if len(cal) < 100:
        sys.exit("窗口内交易日不足")
    print(f"[cal] 交易日历(指数∩ETF并集): {cal[0].date()} ~ {cal[-1].date()} 共 {len(cal)} 日")

    cat_of = {s: (meta_all.get(s, {}).get("cat")
                  or config.CYCLE_SECTORS.get(s, {}).get("cat", "其他")) for s in parts}
    cat_meta = {s: {"cat": cat_of[s]} for s in parts}
    idx_dfs = {s: dfs_all[s] for s in parts}
    act_mask = {s: pd.Series(cal >= first_bar[s], index=cal) for s in parts}
    late = [s for s in parts if first_bar[s] > cal[0]]
    if late:
        print("[cal] ETF 上市晚于窗口起点、从上市日起参与的板块: "
              + ", ".join(f"{s}({first_bar[s].date()})" for s in late))

    common = dict(enter_below=args.enter, exit_above=args.exit,
                  max_positions=args.max_positions, per_cat=args.per_cat,
                  cost_rate=args.cost)

    # ---- B: ETF 版（主结果; --no-limit 时即 B2 口径） ----
    simB = simulate_etf(idx_dfs, cat_meta, etf_close, cal, act_mask,
                        apply_limit=not args.no_limit,
                        label="B ETF版" + ("(无涨跌停)" if args.no_limit else ""),
                        **common)
    simB2 = None
    if not args.no_limit:
        # ---- B2: 同一信号、关闭涨跌停顺延（分离执行约束的影响） ----
        simB2 = simulate_etf(idx_dfs, cat_meta, etf_close, cal, act_mask,
                             apply_limit=False, label="B2 ETF版(无涨跌停)", **common)

    # ---- A: 指数版（直接 run_backtest, 同窗口同参数; 指数无涨停） ----
    # trailing_stop=False: 与 ETF 版信号严格一致（纯温度迟滞 12/75）, 保证 A−B 只反映载体差异
    print("[bt] A 指数版(run_backtest, 参与板块) ...")
    resA = btengine.run_backtest(
        idx_dfs, cat_meta, mode="cold", cost_rate=args.cost,
        max_positions=args.max_positions, per_category_max=args.per_cat,
        trailing_stop=False, start=win_start, end=win_end,
        strat_params={"enter_below": args.enter, "exit_above": args.exit})
    mA = resA["metrics"]
    print(f"[bt] A 指数版: 交易{mA['n_trades']}笔 | 年化{mA['cagr'] * 100:.1f}% | "
          f"回撤{mA['max_drawdown'] * 100:.1f}% | 夏普{mA['sharpe']:.2f}")

    # ---- A+: 全部 30 板块指数版（参考） ----
    resA_all = btengine.run_backtest(
        dfs_all, meta_all, mode="cold", cost_rate=args.cost,
        max_positions=args.max_positions, per_category_max=args.per_cat,
        trailing_stop=False, start=win_start, end=win_end,
        strat_params={"enter_below": args.enter, "exit_above": args.exit})

    # ---- C: 等权持有全部参与 ETF（逐日等权、零成本; 各板块自 ETF 上市日起计入） ----
    px_df = pd.DataFrame({s: etf_close[s].reindex(cal).ffill() for s in parts})
    rets_df = px_df.pct_change(fill_method=None)
    act_df = pd.DataFrame(act_mask)
    daily_c = rets_df.where(act_df).mean(axis=1).fillna(0.0)
    eqC = (1.0 + daily_c).cumprod()
    mC = btengine.compute_metrics(pd.DataFrame({"equity": eqC}, index=cal), None, None)

    # ---- D: 沪深300 买入持有（同日历） ----
    bench = data.get_benchmark()
    if bench is None or len(bench) == 0:
        sys.exit("沪深300基准数据不可用")
    bclose = bench.set_index("date")["close"].reindex(cal).ffill()
    eqD = bclose / bclose.iloc[0]
    mD = btengine.compute_metrics(pd.DataFrame({"equity": eqD}, index=cal), None, None)

    # ---- 对比组指标表 ----
    rows = [_metrics_row("A", f"指数版cold(参与板块n={len(parts)})", mA),
            _metrics_row("A+", "指数版cold(全部30板块, 参考)", resA_all["metrics"])]
    if args.no_limit:
        rows.append(_metrics_row("B", "ETF版cold(无涨跌停, 仅停牌顺延)", simB["metrics"]))
    else:
        rows.append(_metrics_row("B", "ETF版cold(涨停/停牌约束)", simB["metrics"]))
        rows.append(_metrics_row("B2", "ETF版cold(无涨跌停约束)", simB2["metrics"]))
    rows.append(_metrics_row("C", "等权持有全部参与ETF(逐日等权, 零成本)", mC))
    rows.append(_metrics_row("D", "沪深300买入持有", mD))
    cmp_df = pd.DataFrame(rows)

    # ---- 每板块跟踪质量表 ----
    defer_src = simB["defer"]
    track = build_tracking(idx_dfs, etf_close, cal, act_mask, parts, defer_src)

    # ---- 汇总指标 ----
    a_c, b_c = mA["cagr"], simB["metrics"]["cagr"]
    b2_c = simB2["metrics"]["cagr"] if simB2 is not None else None
    drag = a_c - b_c                                  # 跟踪拖累 = A − B
    carrier = a_c - b2_c if b2_c is not None else None   # 纯载体跟踪损耗 = A − B2
    exe = b2_c - b_c if b2_c is not None else None       # 执行约束影响 = B2 − B
    n_buy_def, n_sell_def = int(defer_src["buy"].sum()), int(defer_src["sell"].sum())
    n_abandon = int(defer_src["abandon"].sum())
    corr_ok = track["日收益相关"].dropna()
    n_corr_hi = int((corr_ok >= 0.85).sum())
    # 载体真实错配: 相关<0.85 且 剔极端日后仍<0.85; 仅全量相关低、剔极端后达标 → 指数源极端日噪声
    low_mask = track["日收益相关"].fillna(1.0) < 0.85
    trim_col = track["相关(剔极端日)"].fillna(0.0)
    true_bad = track[low_mask & (trim_col < 0.85)]["板块"].tolist()
    noise_bad = track[low_mask & (trim_col >= 0.85)]["板块"].tolist()
    gap_rows = track[track["指数缺K线"] > 0][["板块", "指数缺K线"]].values.tolist()
    gap_txt = ", ".join(f"{s}({n}日)" for s, n in gap_rows) if gap_rows else "无"

    # ---- 权益曲线对齐 + 绘图 ----
    curves = [("A 指数版cold(参与板块)", resA["equity"]["equity"]),
              ("B ETF版cold(涨停/停牌约束)" if not args.no_limit else "B ETF版cold(无涨跌停)",
               simB["equity"]["equity"])]
    if simB2 is not None:
        curves.insert(2, ("B2 ETF版cold(无涨跌停)", simB2["equity"]["equity"]))
    curves += [("C 等权持有全部ETF", eqC), ("D 沪深300", eqD)]
    aligned = [(lb, s.reindex(cal).ffill()) for lb, s in curves]
    for lb, s in aligned:
        _sanity_curve(lb, s)

    os.makedirs(OUT_DIR, exist_ok=True)
    png_path = os.path.join(OUT_DIR, "etf_curve.png")
    plot_curves(aligned, png_path, logy=args.log)

    # ---- 跟踪质量 CSV ----
    csv_path = os.path.join(OUT_DIR, "etf_tracking.csv")
    track.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # ---- 终端输出 ----
    print("\n" + "=" * 100)
    print(f"对比组指标（窗口 {cal[0].date()} ~ {cal[-1].date()}, cold {args.enter:g}/{args.exit:g}, "
          f"成本{args.cost * 100:.2f}%单边, 最大{args.max_positions}仓等权, 同类≤{args.per_cat}）")
    print("=" * 100)
    print(cmp_df.to_string(index=False))
    print("\n" + "-" * 100)
    print("每板块跟踪质量（指数 vs ETF）:")
    print(track.to_string(index=False))
    print(f"\n[check] 全量日收益相关≥0.85: {n_corr_hi}/{len(corr_ok)} | "
          f"载体真实错配(剔极端日后仍<0.85): {len(true_bad)}个 | 指数源极端日噪声: {len(noise_bad)}个 | "
          f"顺延合计 买{n_buy_def}/卖{n_sell_def} | 放弃买入 {n_abandon} 次")

    # ---- Markdown 报告 ----
    md_path = os.path.join(OUT_DIR, "etf_backtest.md")
    L = []
    L.append("# ETF 载体回测：指数信号 → 可交易 ETF（cold 策略）\n")
    L.append(f"生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}  ")
    L.append(f"窗口: {cal[0].date()} ~ {cal[-1].date()}（{len(cal)} 个交易日）  ")
    L.append(f"参与板块: {len(parts)} 个（config.ETF_MAP, 无 ETF 的板块不参与）  ")
    L.append(f"参数: cold 温度<{args.enter:g}买入 / >{args.exit:g}卖出 | 成本{args.cost * 100:.2f}%单边 | "
             f"最大{args.max_positions}仓等权 | 同类≤{args.per_cat} | "
             f"涨跌停阈值±{LIMIT_TH * 100:.1f}% | 买入顺延上限{MAX_DEFER}日\n")

    L.append("## 一、对比组指标\n")
    L.append(_md_table(cmp_df))
    L.append("")
    L.append(f"- **跟踪拖累 (A−B) = {drag * 100:+.2f}pp**：指数版年化 {a_c * 100:.1f}% → "
             f"ETF版年化 {b_c * 100:.1f}%")
    if b2_c is not None:
        L.append(f"- **分解**: 纯载体跟踪损耗 (A−B2) = {carrier * 100:+.2f}pp；"
                 f"执行约束影响 (B2−B) = {exe * 100:+.2f}pp")
    L.append(f"- **被动基准**: 等权持有全部参与 ETF 年化 {mC['cagr'] * 100:.1f}%、"
             f"沪深300 年化 {mD['cagr'] * 100:.1f}%；"
             f"ETF策略(B) 相对等权ETF 超额 {(b_c - mC['cagr']) * 100:+.1f}pp/年")
    L.append(f"- **执行约束统计**: 买入顺延 {n_buy_def} 次、卖出顺延 {n_sell_def} 次、"
             f"放弃买入 {n_abandon} 次（连续涨停超 {MAX_DEFER} 日）\n")
    L.append("![净值曲线](etf_curve.png)\n")

    L.append("## 二、每板块跟踪质量（指数 vs ETF）\n")
    L.append("日收益相关/beta 只在**指数与 ETF 同日都有真实 K 线**的交易日上计算（同花顺指数源存在缺 K 线, "
             "ffill 成零收益会污染统计）; 相关(剔极端日) = 剔除 |日收益|>4% 双侧极端日后的稳健相关, "
             "用于区分'载体跟踪'与'指数源极端日数据噪声'; 指数缺K线 = 参与期内指数源缺 K 线的天数; "
             "年化差 = 指数年化 − ETF年化（ffill 端点法, 缺口不影响端点）。顺延/放弃统计来自 B 组模拟。\n")
    L.append(_md_table(track))
    L.append("")
    if gap_rows:
        L.append(f"**指数源数据质量提示**: 以下板块的指数源在参与窗口内缺 K 线——{gap_txt}"
                 "（如煤炭 2023 年全年缺失, 属同花顺指数源问题而非 ETF 载体问题; "
                 "相关统计已只在共同真实 K 线日上计算, 不受影响）。模拟中这些日子该板块目标仓位按引擎口径记 0, "
                 "A/B 两组同等受限。\n")
    if true_bad:
        L.append(f"**载体真实错配板块（日收益相关<0.85 且 剔极端日后仍<0.85）**: {', '.join(true_bad)}"
                 "——载体与指数真实错位（结构性错配/近似载体）, 实盘需降权或替代。\n")
    if noise_bad:
        L.append(f"**指数源极端日噪声板块（全量相关<0.85 但 剔极端日后≥0.85, 载体本身可用）**: "
                 f"{', '.join(noise_bad)}\n")

    L.append("## 三、结论（用数据说话）\n")
    L.append(f"1. **跟踪拖累 {drag * 100:+.2f}pp/年**。同一套 cold 信号（12/75）, "
             f"在指数上年化 {a_c * 100:.1f}%, 落到可交易 ETF 上年化 {b_c * 100:.1f}%。"
             + (f"分解: 载体跟踪误差贡献 {carrier * 100:+.2f}pp, 涨跌停/停牌执行约束贡献 {exe * 100:+.2f}pp。"
                if b2_c is not None else ""))
    L.append(f"2. **执行约束影响 {'较小' if exe is not None and abs(exe) < 0.02 else '显著'}**: "
             f"窗口内买入顺延 {n_buy_def} 次、卖出顺延 {n_sell_def} 次、放弃买入 {n_abandon} 次"
             + (f"，对年化影响 {exe * 100:+.2f}pp。" if exe is not None else "。")
             + ("行业 ETF 极少连续涨停/跌停, 该约束主要在极端行情（如复牌补涨、2024/9 底部暴涨）中起作用。"
                if exe is not None and abs(exe) < 0.02 else
                "涨跌停顺延明显改变了成交路径, 需要在实盘执行中预留顺延/放弃规则。"))
    if true_bad or noise_bad:
        parts = []
        if true_bad:
            parts.append(f"**真实载体错配 {len(true_bad)} 个**（全量相关<0.85 且剔极端日后仍<0.85）: "
                         f"{', '.join(true_bad)}——结构性错配（贵金属=金价ETF vs 金矿股、油气=QDII LOF、"
                         "石油加工=原油LOF vs 炼化股）与共用近似载体属此类, 实盘应降权或替代")
        if noise_bad:
            parts.append(f"**指数源极端日噪声 {len(noise_bad)} 个**（全量相关<0.85 但剔极端日后≥0.85, "
                         f"载体本身可用）: {', '.join(noise_bad)}——极端日指数涨幅与 ETF 幅度脱钩, "
                         "属同花顺指数源口径问题, 不代表载体失真")
        L.append("3. **载体失真判定**（以'相关(剔极端日)'列为准）: " + "；".join(parts) + "。")
    else:
        L.append("3. **载体保真度**: 全部参与板块指数-ETF日收益相关性 ≥ 0.85, 信号载体映射整体可靠。")
    L.append(f"4. **ETF 实盘可行性**: 策略 B 年化 {b_c * 100:.1f}%、Sharpe {simB['metrics']['sharpe']:.2f}、"
             f"最大回撤 {simB['metrics']['max_drawdown'] * 100:.1f}%"
             + (f"，相对沪深300 年化超额 {(b_c - mD['cagr']) * 100:+.1f}pp"
                if mD.get("cagr") is not None and np.isfinite(mD.get("cagr", np.nan)) else "")
             + f"。跟踪拖累 {drag * 100:.1f}pp/年 是实盘必须接受的成本"
             + (f"（其中执行约束仅 {exe * 100:.1f}pp, 大头是载体跟踪误差）" if exe is not None else "")
             + "；建议: 真实载体错配板块（" + (", ".join(true_bad) if true_bad else "无")
             + "）降权或替代, 其余板块（含剔极端日相关达标的'噪声'板块）指数信号可直接映射到 ETF 执行。\n")

    L.append("## 四、方法与口径\n")
    L.append("- **信号**: `strategy.position_target(指数df, mode='cold', enter/exit)` —— 与指数版完全同源; "
             "候选排序用 `strategy.signal_strength`（指数侧动量+相对强度+温度斜率）。")
    L.append("- **时序**: 镜像 `run_backtest` 代码行为——T 日收盘用指数数据算信号、T 日收盘价成交"
             "（等价于'信号日收盘调仓, 仓位自 T+1 起承担收益'）; 组合 max_positions 等权、卖出优先、"
             "空位当日可回补、同类上限 per_category_max、成本=当日换手×单边费率。")
    L.append("- **A 组口径**: 直接调用 `run_backtest`(mode='cold', trailing_stop=False), "
             "参与板块与窗口同 B 组, 保证 A−B 只反映载体/执行差异。（关闭移动止损是为了与 ETF 版的"
             "纯温度迟滞信号严格一致。）")
    L.append("- **涨停约束**: 成交日 ETF 当日涨幅 ≥ +9.5% 时买入顺延（连续尝试, 超 10 个交易日放弃并封锁至信号归零）; "
             "跌幅 ≤ −9.5% 时卖出顺延（次日继续尝试, 能卖的第一天卖出）。停牌（当日无 K 线）顺延到下一个有 K 线的日子, "
             "估值用最近可用价 ffill。`--no-limit` 仅关闭涨跌停顺延。")
    L.append("- **价格复权**: ETF 行情取自 `data.get_etf_hist`（adjusted=True）, 已由 `data.adjust_etf_prices` "
             "完成份额折算/合并修复（新浪源不复权, 2021-2026 内 universe 有 6 起单日 ±50~170% 的人为价格缺口——"
             "如 房地产ETF 2024-08-12 合并、煤炭/券商/半导体ETF 拆分——不修复会把折算当暴涨、拆分当腰斩, "
             "收益与相关性完全失真）和分红除息的近似全收益复权（分红数据可见的品种, 约 7/21）。"
             "涨跌停判定、相关性与年化均基于复权后价格。")
    L.append("- **可复现性**: ETF 行情经私有快照缓存 `output/etf_cache/`（首次运行从共享缓存导入后冻结）, "
             "配 `--offline` 只读快照, 隔离其他进程对共享缓存的并发重写; 本任务执行期间共享缓存曾反复变更, "
             "导致同日两次运行结果不同, 快照机制保证本报告数字可复现。每交易日删除快照目录即可换新数据。")
    L.append("- **参与起点**: ETF 上市晚于窗口起点的板块从其 ETF 上市日起参与（如电力ETF 2022 年上市）; "
             "等权基准 C 中各板块亦自上市日起计入。")
    L.append("- **C 组实现**: 逐日等权（当日有数据的参与 ETF 等权平均日收益, 逐日再平衡）、零成本——"
             "最简单的被动基准近似, 未计再平衡成本。")
    L.append("- **跟踪质量口径**: 日收益相关/beta 只用指数与 ETF 同日都有真实 K 线的交易日"
             "（指数源缺 K 线的日期不计入, 避免零收益污染）; '相关(剔极端日)'剔除双侧 |日收益|>4% 的日子; "
             "年化收益用 ffill 端点值计算。指数缺 K 线属同花顺指数源问题（如煤炭 2023 年全年缺失）, "
             "在模拟中按引擎口径该板块目标仓位记 0, A/B 两组同等受限。")
    L.append("- **数据**: 信号= data/enriched.pkl（行业指数富化）; ETF= `data.get_etf_hist`（新浪源, 本地缓存）; "
             "基准= `data.get_benchmark`（沪深300）。单只 ETF 拉取失败自动跳过该板块。\n")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"\n[done] 报告: {md_path}")
    print(f"[done] 曲线: {png_path}" + ("（对数轴）" if args.log else ""))
    print(f"[done] 跟踪表: {csv_path}")
    print(f"[done] 总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
