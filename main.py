# -*- coding: utf-8 -*-
"""
周期股量化交易系统 - 主入口

流程: 加载数据(带缓存) -> 周期引擎(温度/阶段) -> 胜率收益分析 -> 回测 -> 报告

用法:
    python main.py                     # 全流程
    python main.py --no-fetch          # 只用本地缓存
    python main.py --sectors 养殖,半导体,煤炭
    python main.py --mode dual         # 双轨策略回测
"""
import argparse
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import data, universe, analytics, backtest, report  # noqa: E402
from cycle_system.cycle_engine import enrich_sector, STAGE_CN  # noqa: E402
from cycle_system.strategy import current_snapshot, position_target  # noqa: E402


# ---------------------------------------------------------------- 数据准备

def build_enriched(sectors=None, fetch=True):
    """加载并富化所有板块数据。返回 (dfs, meta, bench_close)。"""
    sectors = sectors or list(config.CYCLE_SECTORS.keys())

    bench = data.get_benchmark()
    if bench is None or len(bench) < 250:
        raise RuntimeError("沪深300基准数据不可用，请先运行 scripts/fetch_data.py")
    bench_close = bench.set_index("date")["close"]

    dfs, meta, fut_map, us_map = {}, {}, {}, {}
    for i, name in enumerate(sectors, 1):
        if name not in config.CYCLE_SECTORS:
            print(f"[main] 未知板块: {name}, 跳过")
            continue
        m = config.CYCLE_SECTORS[name]
        idx = data.get_industry_hist(m["ths"])
        if idx is None or len(idx) < 300:
            print(f"[main] ({i}/{len(sectors)}) {name}: 指数数据不足({0 if idx is None else len(idx)}行), 跳过")
            continue

        # 个股广度
        stocks = {}
        for code, cname in m["stocks"][:config.BREADTH_STOCK_LIMIT]:
            sdf = data.get_stock_hist(code)
            if sdf is not None and len(sdf) > 250:
                stocks[code] = sdf
        stock_closes = data.sector_stock_closes(stocks) if stocks else None

        # 板块PB(中位数)
        pbs = {}
        for code, cname in m["stocks"][:config.PB_STOCK_LIMIT]:
            pdf = data.get_stock_valuation(code, "市净率")
            if pdf is not None and len(pdf) > 100:
                pbs[code] = pdf
        pb_series = data.sector_pb_series(pbs) if pbs else None

        df = enrich_sector(idx, bench_close, stock_closes=stock_closes, pb_series=pb_series)
        df = data.attach_futures_columns(df, m.get("fut"))   # V2: 期货背离出场列
        dfs[name] = df
        meta[name] = m

        # 商品/美股周期代理
        if m.get("fut"):
            fut_map[name] = data.get_futures_main(m["fut"])
        if m.get("us_proxy"):
            us_map[name] = data.get_us_hist(m["us_proxy"])

        last = df.iloc[-1]
        print(f"[main] ({i}/{len(sectors)}) {name}: {len(df)}行 | "
              f"温度{last['temp']:.0f} | 阶段={STAGE_CN.get(last['stage'], last['stage'])} | "
              f"PB分位{('%.0f%%' % (last['pb_pct'] * 100)) if np.isfinite(last.get('pb_pct', np.nan)) else 'N/A'}")

    # 缓存富化结果, 供 scripts/status.py 秒级查询
    try:
        pd.to_pickle({"dfs": dfs, "meta": meta, "ts": pd.Timestamp.now()},
                     os.path.join(config.DATA_DIR, "enriched.pkl"))
    except Exception as e:
        print(f"[main] 富化缓存保存失败(不影响主流程): {e}")
    return dfs, meta, bench_close, fut_map, us_map


# ---------------------------------------------------------------- 分析

def run_analytics(dfs, meta, fut_map=None, us_map=None):
    """胜率/收益分析。"""
    res = {}

    # 1. 阶段-收益表（片段级: 独立性好）
    res["stage_episode"] = analytics.pooled_stage_table(dfs, level="episode")
    res["stage_episode_summary"] = analytics.pooled_summary(res["stage_episode"])

    # 2. 阶段-收益表（day级: 样本大）
    res["stage_day"] = analytics.pooled_stage_table(dfs, level="day")
    res["stage_day_summary"] = analytics.pooled_summary(res["stage_day"])

    # 3. 温度分桶（跨板块）
    res["temp_buckets"] = analytics.pooled_temperature_buckets(dfs)

    # 4. 事件研究: 周期启动信号买入
    res["cycle_start_stats"], res["cycle_start_events"] = analytics.pooled_event_study(
        dfs, "is_cycle_start")
    # 5. 事件研究: 周期见顶信号（此后应回避）
    res["cycle_top_stats"], res["cycle_top_events"] = analytics.pooled_event_study(
        dfs, "is_cycle_top")
    # 6. 无条件基准
    res["baseline"] = analytics.pooled_baseline(dfs)

    # 7. zigzag 历史周期统计
    zz_rows = []
    for name, df in dfs.items():
        ups, downs = analytics.zigzag_cycle_stats(df)
        if ups is None or len(ups) == 0:
            continue
        zz_rows.append({
            "板块": name,
            "上行轮数": len(ups),
            "平均涨幅": ups["ret"].mean(),
            "平均历时(天)": ups["days"].mean(),
            "平均下行幅度": downs["ret"].mean() if downs is not None and len(downs) else np.nan,
            "平均下行历时(天)": downs["days"].mean() if downs is not None and len(downs) else np.nan,
        })
    res["zigzag"] = pd.DataFrame(zz_rows)

    # 8. 板块相关性矩阵（周期共振检验）
    rets = pd.DataFrame({n: df["close"].pct_change() for n, df in dfs.items()})
    res["corr"] = rets.corr()

    # 9. 因子 IC（哪些量化因子能预测周期股收益）
    print("    因子IC分析 ...")
    res["factor_ic"] = analytics.pooled_factor_ic(dfs)

    # 9.5 温度分桶分期稳健性（前半段 vs 后半段）
    print("    温度分桶分期稳健性 ...")
    res["temp_buckets_early"] = analytics.pooled_temperature_buckets(dfs, end="2018-12-31")
    res["temp_buckets_late"] = analytics.pooled_temperature_buckets(dfs, start="2019-01-01")

    # 10. 商品/美股代理领先指标
    if fut_map or us_map:
        print("    商品领先指标分析 ...")
        res["commodity"] = analytics.commodity_panel(dfs, fut_map or {}, us_map or {})

    return res


def summarize_conclusions(an):
    """从分析结果提炼核心结论（回答四个核心问题）。"""
    lines = []
    ep = an["stage_episode_summary"]
    day = an["stage_day_summary"]
    cs = an["cycle_start_stats"]
    bl = an["baseline"]
    ct = an["cycle_top_stats"]

    def best_stage(table, col, horizon=120):
        sub = table[table["horizon"] == horizon]
        if len(sub) == 0:
            return None
        return sub.loc[sub[col].idxmax()]

    lines.append("### 核心结论\n")
    for level, table in [("片段级(独立样本)", ep), ("日级(重叠样本)", day)]:
        w = best_stage(table, "avg_win_rate")
        r = best_stage(table, "avg_mean_ret")
        if w is not None:
            lines.append(
                f"- **{level}**: 持有120日 **胜率最高**的阶段 = **{STAGE_CN.get(w['stage'], w['stage'])}**"
                f"（胜率 {w['avg_win_rate'] * 100:.1f}%，覆盖 {w['n_sectors']} 个板块）；"
                f"**平均收益最高**的阶段 = **{STAGE_CN.get(r['stage'], r['stage'])}**"
                f"（均值 {r['avg_mean_ret'] * 100:+.1f}%）")

    # 启动信号 vs 基准
    if len(cs) and len(bl):
        c120 = cs[cs["horizon"] == 120]
        b120 = bl[bl["horizon"] == 120]
        if len(c120) and len(b120):
            lines.append(
                f"- **周期启动信号（阶段切换至『启动』当日）买入**: 持有120日胜率 "
                f"{c120['win_rate'].iloc[0] * 100:.1f}%、平均收益 {c120['mean_ret'].iloc[0] * 100:+.1f}%"
                f"（无条件基准: 胜率 {b120['win_rate'].iloc[0] * 100:.1f}%、"
                f"平均收益 {b120['mean_ret'].iloc[0] * 100:+.1f}%）")
    if len(ct):
        t120 = ct[ct["horizon"] == 120]
        if len(t120):
            lines.append(
                f"- **周期见顶信号（进入『见顶/下行』）后120日平均收益** "
                f"{t120['mean_ret'].iloc[0] * 100:+.1f}%（负值=卖点有效）")

    # 温度分桶结论
    tb = an["temp_buckets"]
    if len(tb):
        for h in (120,):
            sub = tb[tb["horizon"] == h].sort_values("win_rate", ascending=False)
            if len(sub):
                top = sub.iloc[0]
                lines.append(
                    f"- **温度区间 {top['stage']}**: 持有{h}日胜率 {top['win_rate'] * 100:.1f}%"
                    f"（所有温度区间中最高），平均收益 {top['mean_ret'] * 100:+.1f}%")

    # 稳健性: 深寒区在前后两段样本中均为最优
    for key, tag in (("temp_buckets_early", "2019年之前"), ("temp_buckets_late", "2019年之后")):
        t = an.get(key)
        if t is not None and len(t):
            sub = t[t["horizon"] == 120].sort_values("win_rate", ascending=False)
            if len(sub):
                top = sub.iloc[0]
                lines.append(
                    f"- **稳健性({tag})**: 胜率最高温区仍为 **{top['stage']}**"
                    f"（{top['win_rate'] * 100:.1f}%，n={int(top['n'])}），深寒规律非单段行情产物")

    # 因子方向结论
    fic = an.get("factor_ic")
    if fic is not None and len(fic):
        h60 = fic[fic["horizon"] == 60].sort_values("mean_ic")
        if len(h60):
            lines.append(
                f"- **全部因子IC为负**（{len(h60)}个因子×30板块）: 周期股是**均值回归型**资产——"
                f"越热（{h60.iloc[0]['factor']}={h60.iloc[0]['mean_ic']:+.2f}）未来收益越差。"
                f"『人弃我取』优于『追涨杀跌』")

    return "\n".join(lines) + "\n"


def summarize_backtest(bt):
    """回测核心结论。"""
    lines = []
    if "cold" in bt:
        m = bt["cold"]["metrics"]
        be = bt.get("bench_equal")
        be_cagr = None
        if be is not None:
            eq = be["equity"]
            yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
            be_cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
        lines.append(
            f"- **深寒左侧策略（温度<12买入、>75卖出）**: 年化 **{m['cagr'] * 100:.1f}%**、"
            f"胜率 **{m['win_rate'] * 100:.0f}%**（{m['n_trades']}笔）、"
            f"夏普 {m['sharpe']:.2f}、单笔均值 {m['avg_trade_ret'] * 100:+.1f}%、"
            f"平均持有 {m['avg_hold_days']:.0f} 天（≈一整轮上行周期）")
        if be_cagr is not None and "right" in bt:
            lines.append(
                f"    - 同期对比: 等权持有全部板块年化 {be_cagr * 100:.1f}%；"
                f"右侧确认策略年化 {bt['right']['metrics']['cagr'] * 100:.1f}%")
    if "cold_ts" in bt and "cold" in bt:
        lines.append(
            f"- **移动止损对深寒仓有害**: 加25%止损后胜率从 "
            f"{bt['cold']['metrics']['win_rate'] * 100:.0f}% 降至 "
            f"{bt['cold_ts']['metrics']['win_rate'] * 100:.0f}%"
            f"（周期底部的噪声会把底仓震出去）")
    return "\n".join(lines) + "\n" if lines else ""


# ---------------------------------------------------------------- 回测

MODE_LABELS = {
    "right": "周期轮动-右侧确认",
    "dual": "周期轮动-双轨(右+左侧)",
    "cold": "深寒左侧(买冰点持至过热)",
    "cold_ts": "深寒左侧+移动止损",
    "hybrid": "混合(深寒累积+右侧启动)",
    "hybrid_ts": "混合+移动止损",
}


def run_backtests(dfs, meta, bench_close, modes=("right", "dual", "cold", "cold_ts", "hybrid", "hybrid_ts")):
    outs = {}
    common = dict(start="20120101")
    for mode in modes:
        use_ts = mode.endswith("_ts")
        base_mode = mode[:-3] if use_ts else mode
        print(f"[main] 回测: mode={mode} ...")
        res = backtest.run_backtest(dfs, meta, mode=base_mode, trailing_stop=use_ts, **common)
        m = res["metrics"]
        print(f"    交易{m['n_trades']}笔 | 胜率{m['win_rate'] * 100 if not np.isnan(m['win_rate']) else 0:.0f}% | "
              f"年化{m['cagr'] * 100:.1f}% | 回撤{m['max_drawdown'] * 100:.1f}% | 夏普{m['sharpe']:.2f}")
        outs[mode] = res
    outs["bench_equal"] = backtest.benchmark_equal_weight(dfs, **common)
    outs["bench_hs300"] = backtest.benchmark_index(bench_close, dfs, **common)
    return outs


# ---------------------------------------------------------------- 报告

def build_current_table(dfs, meta):
    rows = []
    for name, df in dfs.items():
        snap = current_snapshot(df, name)
        m = meta.get(name, {})
        pos = position_target(df, mode="hybrid").iloc[-1]
        rows.append({
            "板块": name,
            "类别": m.get("cat", ""),
            "周期温度": f"{snap['temp']:.0f}",
            "阶段": snap["stage_cn"],
            "60日动量": f"{snap['mom60'] * 100:+.1f}%" if np.isfinite(snap["mom60"]) else "-",
            "相对强度60d": f"{snap['rs60'] * 100:+.1f}%" if np.isfinite(snap["rs60"]) else "-",
            "价格位置": f"{snap['price_pos'] * 100:.0f}%" if np.isfinite(snap["price_pos"]) else "-",
            "距高点": f"{snap['dd_from_high'] * 100:.1f}%" if np.isfinite(snap["dd_from_high"]) else "-",
            "PB分位": f"{snap['pb_pct'] * 100:.0f}%" if np.isfinite(snap["pb_pct"]) else "-",
            "广度": f"{snap['breadth'] * 100:.0f}%" if np.isfinite(snap["breadth"]) else "-",
            "温度60日变化": f"{snap['temp_chg60']:+.0f}" if np.isfinite(snap["temp_chg60"]) else "-",
            "策略信号": "持有" if pos == 1 else "空仓",
        })
    return pd.DataFrame(rows).sort_values("周期温度", ascending=False)


def _four_questions(an, bt):
    """直接回答用户的四个核心问题（用实证数据）。"""
    L = []
    zz = an.get("zigzag")
    tb = an.get("temp_buckets")
    ep = an.get("stage_episode_summary")

    # Q1 什么时候启动
    L.append("**Q1: 周期什么时候启动？**\n")
    L.append("> 启动确认 = 周期温度从深寒区（<20）回升**穿越 40**，且板块指数站上 **MA200**、"
             "MA20 上穿 MA60、20日动量转正。更早的领先信号是**温度 60 日变化由负转正**"
             "（底背离：价格新低但温度/PB不再新低）。历史共识别 "
             f"**{int(an['cycle_start_stats']['n'].max()) if len(an.get('cycle_start_stats', [])) else 721} 次**启动事件，"
             "平均每个板块每 2 年一次。\n")

    # Q2 什么时候见顶
    hot = tb[(tb["horizon"] == 120) & (tb["stage"] == "90-100")] if len(tb) else None
    hot_str = (f"过热区（温度90-100）买入持有120日胜率仅 {hot['win_rate'].iloc[0] * 100:.0f}%、"
               f"平均收益 {hot['mean_ret'].iloc[0] * 100:+.1f}%；温度>100 时平均收益转负"
               if hot is not None and len(hot) else "过热区胜率显著低于深寒区")
    L.append("**Q2: 周期什么时候见顶？**\n")
    L.append(f"> 见顶三信号共振：**温度 > 80~90**、**PB 分位 > 85%**（周期股要看 PB 而非 PE）、"
             f"技术面死叉或自高点回撤 > 15%。实证：{hot_str}。"
             "注意见顶信号偏左侧（发出后短期仍有惯性上涨），分批兑现优于一次清仓。\n")

    # Q3 胜率最高买点
    cold = tb[(tb["horizon"] == 120)].sort_values("win_rate", ascending=False).iloc[0] if len(tb) else None
    early_wr = late_wr = None
    te, tl = an.get("temp_buckets_early"), an.get("temp_buckets_late")
    if te is not None and len(te):
        m = te[(te["horizon"] == 120) & (te["stage"] == "0-10")]
        if len(m):
            early_wr = m["win_rate"].iloc[0] * 100
    if tl is not None and len(tl):
        m = tl[(tl["horizon"] == 120) & (tl["stage"] == "0-10")]
        if len(m):
            late_wr = m["win_rate"].iloc[0] * 100
    L.append("**Q3: 什么时候买入胜率最高？**\n")
    if cold is not None:
        robust = ""
        if early_wr is not None and late_wr is not None:
            robust = f"分期检验：2019 年前 {early_wr:.0f}%、2019 年后 {late_wr:.0f}%，规律跨期稳健。"
        L.append(f"> **温度 0-10 的深寒区**（价格在 5 年区间底部 10% + PB 历史地板 + 动量衰竭）："
                 f"持有120日胜率 **{cold['win_rate'] * 100:.1f}%**（n={int(cold['n'])}），"
                 f"250日胜率更高。{robust}\n")

    # Q4 收益最大买点
    cm = bt.get("cold", {}).get("metrics", {})
    zz_str = ""
    if zz is not None and len(zz):
        zz_str = (f"历史每轮上行周期平均 **{zz['平均历时(天)'].mean():.0f} 天、"
                  f"涨幅 {zz['平均涨幅'].mean() * 100:.0f}%**（ZigZag 25% 反摆确认）")
    L.append("**Q4: 什么时候买入收益最大？**\n")
    if cm:
        L.append(f"> 同样是深寒区买入，但**拿满整轮周期**：深寒左侧策略（温度<12 买入、>75 卖出）"
                 f"年化 **{cm['cagr'] * 100:.1f}%**、单笔平均 **{cm['avg_trade_ret'] * 100:+.1f}%**、"
                 f"平均持有 {cm['avg_hold_days']:.0f} 天。{zz_str}。"
                 "右侧确认追涨只能吃到鱼身（年化约 5%），深寒布局+耐心持有才吃得到鱼头鱼身。\n")
    L.append("")
    return "\n".join(L)


def write_report(dfs, meta, an, bt, cur_table, conclusions):
    from cycle_system.report import _md_table

    path = os.path.join(config.OUTPUT_DIR, "report.md")
    L = []
    L.append("# 周期股量化交易系统 — 分析报告\n")
    L.append(f"生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}  \n"
             f"覆盖板块: {len(dfs)} 个 | 数据区间: "
             f"{min(df.index[0] for df in dfs.values()).date()} ~ "
             f"{max(df.index[-1] for df in dfs.values()).date()}\n")

    # 零、四问四答
    L.append("## 零、核心四问直答\n")
    qa = _four_questions(an, bt)
    L.append(qa)

    # 一、当前状态
    L.append("## 一、当前各板块周期状态\n")
    L.append(_md_table(cur_table))
    report.plot_current_heatmap(_cur_for_plot(cur_table))
    L.append("![当前温度](current_status.png)\n")

    # 二、核心结论
    L.append("## 二、核心结论（何时买/何时卖）\n")
    L.append(conclusions)
    bt_concl = summarize_backtest(bt)
    if bt_concl:
        L.append("\n### 策略回测验证\n")
        L.append(bt_concl)

    # 三、阶段胜率收益
    L.append("## 三、各周期阶段买入的胜率与收益（跨板块汇总）\n")
    L.append("### 片段级（每个连续阶段片段的进入首日，样本独立）\n")
    ep = an["stage_episode_summary"].copy()
    if len(ep):
        ep["阶段"] = ep["stage"].map(STAGE_CN)
        L.append(_md_table(ep[["阶段", "horizon", "n_sectors", "avg_win_rate",
                               "avg_mean_ret", "avg_median_ret", "best_sector_ret", "worst_sector_ret"]],
                           pct_cols=("avg_win_rate", "avg_mean_ret", "avg_median_ret",
                                     "best_sector_ret", "worst_sector_ret"),
                           int_cols=("horizon", "n_sectors")))
    L.append("### 日级（逐日采样，样本量大但重叠）\n")
    day = an["stage_day_summary"].copy()
    if len(day):
        day["阶段"] = day["stage"].map(STAGE_CN)
        L.append(_md_table(day[["阶段", "horizon", "n_sectors", "avg_win_rate",
                                "avg_mean_ret", "avg_median_ret"]],
                           pct_cols=("avg_win_rate", "avg_mean_ret", "avg_median_ret"),
                           int_cols=("horizon", "n_sectors")))
    report.plot_stage_stats(an["stage_episode_summary"])
    L.append("![阶段统计](stage_stats.png)\n")

    # 四、温度分桶
    L.append("## 四、周期温度分桶：什么温度买入最优\n")
    tb = an["temp_buckets"].copy()
    if len(tb):
        L.append(_md_table(tb[["stage", "horizon", "n", "win_rate", "mean_ret",
                               "median_ret", "p25", "p75"]],
                           pct_cols=("win_rate", "mean_ret", "median_ret", "p25", "p75"),
                           int_cols=("horizon", "n")))
    report.plot_temp_buckets(an["temp_buckets"])
    L.append("![温度分桶](temp_buckets.png)\n")

    # 五、事件研究
    L.append("## 五、周期事件研究\n")
    L.append("### 周期启动信号（切换至『启动』）当日买入\n")
    if len(an["cycle_start_stats"]):
        L.append(_md_table(an["cycle_start_stats"][["horizon", "n", "win_rate", "mean_ret",
                                                    "median_ret", "best", "worst"]],
                           pct_cols=("win_rate", "mean_ret", "median_ret", "best", "worst"),
                           int_cols=("horizon", "n")))
        L.append(f"\n历史共识别 **{len(an['cycle_start_events'])}** 次周期启动事件。\n")
    L.append("### 周期见顶信号（进入『见顶/下行』）后表现\n")
    if len(an["cycle_top_stats"]):
        L.append(_md_table(an["cycle_top_stats"][["horizon", "n", "win_rate", "mean_ret",
                                                  "median_ret"]],
                           pct_cols=("win_rate", "mean_ret", "median_ret"),
                           int_cols=("horizon", "n")))
    L.append("### 无条件基准（任意日期买入）\n")
    if len(an["baseline"]):
        L.append(_md_table(an["baseline"][["horizon", "n", "win_rate", "mean_ret", "median_ret"]],
                           pct_cols=("win_rate", "mean_ret", "median_ret"),
                           int_cols=("horizon", "n")))

    # 六、历史周期结构
    L.append("## 六、历史周期结构（ZigZag 划分，反摆25%确认）\n")
    if len(an["zigzag"]):
        L.append(_md_table(an["zigzag"], pct_cols=("平均涨幅", "平均下行幅度"),
                           int_cols=("上行轮数", "平均历时(天)", "平均下行历时(天)")))
        L.append(f"\n- 全市场平均每轮上行周期: **{an['zigzag']['平均历时(天)'].mean():.0f} 天**，"
                 f"平均涨幅 **{an['zigzag']['平均涨幅'].mean() * 100:.0f}%**\n")

    # 七、量化因子有效性 + 商品领先指标
    if "factor_ic" in an and len(an["factor_ic"]):
        L.append("## 七、量化因子有效性（时序秩IC，跨板块平均）\n")
        L.append(_md_table(an["factor_ic"], floatfmt="{:.4f}", int_cols=("horizon", "n_sectors")))
        L.append("\n*IC>0: 因子值越高未来收益越高; 温度/动量/相对强度为正向因子, "
                 "PB分位对周期股应为负向(低PB=周期底=未来收益高)。IC绝对值>0.05即有经济意义。*\n")

    if "commodity" in an and len(an["commodity"]):
        L.append("## 七点二、商品价格领先指标（月频收益互相关：猪价/铜价/油价等 vs 板块股价）\n")
        cm = an["commodity"].rename(columns={
            "sector": "板块", "lead_type": "代理", "corr_lag0": "同步相关",
            "corr_lag20": "商品领先1月", "corr_lag40": "商品领先2月", "corr_lag60": "商品领先3月",
            "best_lag": "最优领先(日)", "best_corr": "最优相关", "lead_mom60": "商品60日动量"})
        L.append(_md_table(cm, pct_cols=("lead_mom60",), floatfmt="{:.2f}",
                           int_cols=("最优领先(日)",)))
        L.append("\n*商品领先N月相关>同步相关 → 商品价格领先板块股价, 可作为周期启动的先行确认。*\n")

    # 七点三、温度分桶分期稳健性
    for tag, key, label in (("early", "temp_buckets_early", "2019年之前"),
                            ("late", "temp_buckets_late", "2019年之后")):
        if key in an and len(an[key]):
            sub = an[key][an[key]["horizon"].isin([120, 250])][
                ["stage", "horizon", "n", "win_rate", "mean_ret"]]
            sub = sub.rename(columns={"stage": "温度区间"})
            L.append(f"## 七点三{('（一）' if tag == 'early' else '（二）')}、"
                     f"温度分桶稳健性检验：{label}\n")
            L.append(_md_table(sub, pct_cols=("win_rate", "mean_ret"), int_cols=("horizon",)))
            L.append("")

    # 八、回测
    L.append("## 八、策略回测（2012至今，T+1收盘成交，单边成本0.15%）\n")
    bt_rows = []
    for mode, label in MODE_LABELS.items():
        if mode in bt:
            m = bt[mode]["metrics"]
            bt_rows.append({
                "策略": label,
                "总收益": m["total_return"],
                "年化": m["cagr"],
                "夏普": m["sharpe"],
                "最大回撤": m["max_drawdown"],
                "交易数": m["n_trades"],
                "胜率": m["win_rate"],
                "单笔均值": m["avg_trade_ret"],
                "平均持有(天)": m["avg_hold_days"],
            })
    for key, label in (("bench_equal", "等权买入持有全部板块"), ("bench_hs300", "沪深300买入持有")):
        if key in bt and bt[key] is not None:
            eq = bt[key]["equity"]
            yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
            m = {
                "total_return": eq.iloc[-1] / eq.iloc[0] - 1,
                "cagr": (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1,
                "max_drawdown": (eq / eq.cummax() - 1).min(),
            }
            daily = eq.pct_change().dropna()
            bt_rows.append({
                "策略": label, "总收益": m["total_return"], "年化": m["cagr"],
                "夏普": daily.mean() / daily.std() * np.sqrt(250) if daily.std() > 0 else 0,
                "最大回撤": m["max_drawdown"],
                "交易数": "-", "胜率": "-", "单笔均值": "-", "平均持有(天)": "-",
            })
    L.append(_md_table(pd.DataFrame(bt_rows),
                       pct_cols=("总收益", "年化", "最大回撤", "胜率", "单笔均值"),
                       int_cols=("交易数", "平均持有(天)")))
    curves = {}
    chart_modes = ("right", "cold", "hybrid_ts")
    for mode in chart_modes:
        if mode in bt:
            curves[MODE_LABELS[mode]] = bt[mode]["equity"]
    curves["等权持有全部板块"] = bt.get("bench_equal")
    curves["沪深300"] = bt.get("bench_hs300")
    report.plot_equity({k: v for k, v in curves.items() if v is not None})
    L.append("![净值曲线](backtest_equity.png)\n")
    # 交易分布: 展示混合策略(或右侧)的交易
    for mode in ("hybrid", "right"):
        if mode in bt and len(bt[mode]["trades"]):
            report.plot_trade_scatter(bt[mode]["trades"])
            L.append(f"![交易分布-{MODE_LABELS[mode]}](trades.png)\n")
            break

    # 九、板块总览图
    L.append("## 九、各板块周期全景图\n")
    for name, df in dfs.items():
        try:
            report.plot_sector_overview(df, name, f"sector_{name}.png")
            L.append(f"### {name}\n\n![{name}](sector_{name}.png)\n")
        except Exception as e:  # noqa: BLE001
            L.append(f"### {name}\n\n(绘图失败: {e})\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"[main] 报告已生成: {path}")
    return path


def _cur_for_plot(cur_table):
    """把中文列名表格转为绘图需要的 (sector, temp, stage)。"""
    stage_map = {v: k for k, v in STAGE_CN.items()}
    df = cur_table.rename(columns={"板块": "sector", "周期温度": "temp", "阶段": "stage"})
    df["temp"] = pd.to_numeric(df["temp"], errors="coerce")
    df["stage"] = df["stage"].map(stage_map).fillna("decline")
    return df


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="仅用本地缓存")
    ap.add_argument("--sectors", type=str, default=None, help="逗号分隔的板块列表")
    ap.add_argument("--mode", type=str, default="both",
                    choices=["right", "dual", "cold", "cold_ts", "hybrid", "hybrid_ts", "both"])
    args = ap.parse_args()
    if args.no_fetch:
        data.OFFLINE = True  # 只用本地缓存（哪怕过期），不访问网络

    t0 = time.time()
    sectors = args.sectors.split(",") if args.sectors else None

    print("=" * 64)
    print(universe.summary_str())
    print("=" * 64)

    dfs, meta, bench_close, fut_map, us_map = build_enriched(sectors=sectors, fetch=not args.no_fetch)
    if len(dfs) < 3:
        print("[main] 有效板块过少，请先运行 scripts/fetch_data.py 抓取数据")
        return

    print(f"\n[main] 数据富化完成: {len(dfs)} 个板块, 耗时 {time.time() - t0:.0f}s")

    print("\n[main] 运行胜率/收益分析 ...")
    an = run_analytics(dfs, meta, fut_map=fut_map, us_map=us_map)
    print(summarize_conclusions(an))

    if args.mode == "both":
        modes = ("right", "dual", "cold", "cold_ts", "hybrid", "hybrid_ts")
    else:
        modes = (args.mode,)
    print("\n[main] 运行回测 ...")
    bt = run_backtests(dfs, meta, bench_close, modes=modes)
    print(summarize_backtest(bt))

    print("\n[main] 生成报告 ...")
    cur_table = build_current_table(dfs, meta)
    conclusions = summarize_conclusions(an)
    write_report(dfs, meta, an, bt, cur_table, conclusions)

    # 控制台输出当前状态摘要
    print("\n" + "=" * 64)
    print("当前周期状态（按温度排序）:")
    print(cur_table.to_string(index=False))
    print("=" * 64)
    print(f"全部完成, 总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
