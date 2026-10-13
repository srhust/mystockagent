# -*- coding: utf-8 -*-
"""
历史周期划分与胜率/收益分析。

核心问题：
1. 周期什么时候启动/见顶？（阶段切换事件 + zigzag 周期段）
2. 什么时候买入胜率最高？（按阶段/温度分桶的未来收益胜率）
3. 什么时候买入收益最大？（同上，看均值/中位数收益）
"""
import numpy as np
import pandas as pd

import config
from . import factors


# ---------------- 未来收益 ----------------

def forward_returns(close: pd.Series, horizons=(20, 60, 120, 250)) -> pd.DataFrame:
    out = {}
    for h in horizons:
        out[f"fwd{h}"] = close.shift(-h) / close - 1
    return pd.DataFrame(out, index=close.index)


# ---------------- 阶段片段（episode） ----------------

def stage_episodes(stage: pd.Series) -> pd.DataFrame:
    """将逐日阶段序列切分为连续片段: [(stage, start, end, days)]。"""
    s = stage.dropna()
    if len(s) == 0:
        return pd.DataFrame()
    chg = (s != s.shift(1)).cumsum()
    rows = []
    for _, g in s.groupby(chg):
        rows.append({
            "stage": g.iloc[0],
            "start": g.index[0],
            "end": g.index[-1],
            "days": len(g),
        })
    return pd.DataFrame(rows)


# ---------------- 按阶段统计未来收益 ----------------

def stage_return_table(df: pd.DataFrame, horizons=(20, 60, 120, 250),
                       level="episode") -> pd.DataFrame:
    """按周期阶段统计买入并持有 N 日的收益分布。

    level='episode' : 每个连续阶段片段取进入首日（样本独立性好，推荐）
    level='day'     : 逐日采样（样本大但重叠相关）
    """
    fr = forward_returns(df["close"], horizons)
    if level == "day":
        d = df[["stage"]].join(fr)
        d = d[d["stage"] != "warmup"].dropna(subset=["stage"])
        rows = []
        for stage, g in d.groupby("stage"):
            for h in horizons:
                s = g[f"fwd{h}"].dropna()
                if len(s) < 5:
                    continue
                rows.append(_stat_row(stage, h, s))
        return pd.DataFrame(rows)

    eps = stage_episodes(df["stage"])
    eps = eps[eps["stage"] != "warmup"]
    rows = []
    for _, ep in eps.iterrows():
        if ep["start"] not in fr.index:
            continue
        row = fr.loc[ep["start"]]
        rows.append({"stage": ep["stage"], **{f"fwd{h}": row[f"fwd{h}"] for h in horizons}})
    obs = pd.DataFrame(rows)
    out = []
    for stage, g in obs.groupby("stage"):
        for h in horizons:
            s = g[f"fwd{h}"].dropna()
            if len(s) < 3:
                continue
            out.append(_stat_row(stage, h, s))
    return pd.DataFrame(out)


def _stat_row(stage, horizon, s: pd.Series):
    return {
        "stage": stage,
        "horizon": horizon,
        "n": len(s),
        "win_rate": float((s > 0).mean()),
        "mean_ret": float(s.mean()),
        "median_ret": float(s.median()),
        "p25": float(s.quantile(0.25)),
        "p75": float(s.quantile(0.75)),
        "best": float(s.max()),
        "worst": float(s.min()),
    }


# ---------------- 温度分桶分析 ----------------

def temperature_bucket_table(df: pd.DataFrame, horizons=(60, 120, 250),
                             bucket=10) -> pd.DataFrame:
    """按周期温度十分桶统计未来收益 —— 直接回答"什么温度买入胜率最高/收益最大"。"""
    fr = forward_returns(df["close"], horizons)
    d = df[["temp", "stage"]].join(fr).dropna(subset=["temp"])
    d = d[d["stage"] != "warmup"]
    d["bucket"] = (d["temp"] // bucket).astype(int) * bucket
    rows = []
    for b, g in d.groupby("bucket"):
        for h in horizons:
            s = g[f"fwd{h}"].dropna()
            if len(s) < 10:
                continue
            r = _stat_row(f"{int(b)}-{int(b) + bucket}", h, s)
            r["stage_num"] = int(b)
            rows.append(r)
    return pd.DataFrame(rows)


def pooled_temperature_buckets(dfs: dict, horizons=(60, 120, 250), bucket=10,
                               start=None, end=None):
    """跨板块汇总温度分桶（day 级采样）。可用 start/end 做分期稳健性检验。"""
    frames = []
    for name, df in dfs.items():
        fr = forward_returns(df["close"], horizons)
        d = df[["temp", "stage"]].join(fr).dropna(subset=["temp"])
        d = d[d["stage"] != "warmup"]
        if start:
            d = d[d.index >= pd.Timestamp(start)]
        if end:
            d = d[d.index <= pd.Timestamp(end)]
        d["bucket"] = (d["temp"] // bucket).astype(int) * bucket
        frames.append(d)
    if not frames:
        return pd.DataFrame()
    allx = pd.concat(frames)
    rows = []
    for b, g in allx.groupby("bucket"):
        for h in horizons:
            s = g[f"fwd{h}"].dropna()
            if len(s) < 30:
                continue
            r = _stat_row(f"{int(b)}-{int(b) + bucket}", h, s)
            r["stage_num"] = int(b)
            rows.append(r)
    return pd.DataFrame(rows)


# ---------------- 阶段切换事件研究 ----------------

def transition_events(df: pd.DataFrame) -> pd.DataFrame:
    """阶段切换事件（含启动/见顶两类核心事件标记）。"""
    tr = pd.DataFrame()
    s = df["stage"].dropna()
    if len(s) == 0:
        return tr
    ch = s[s != s.shift(1)]
    events = []
    vals, idx = ch.values, ch.index
    for i in range(1, len(vals)):
        events.append({"date": idx[i], "from": vals[i - 1], "to": vals[i]})
    tr = pd.DataFrame(events)
    if len(tr) == 0:
        return tr
    tr["is_cycle_start"] = (tr["to"] == "recovery") & (tr["from"].isin(["bottom", "decline"]))
    tr["is_cycle_top"] = (tr["to"] == "topping") | (
        (tr["to"] == "decline") & tr["from"].isin(["expansion", "recovery", "topping"]))
    return tr


def event_return_study(df: pd.DataFrame, event_mask_col, horizons=(20, 60, 120, 250)):
    """事件（如周期启动）日买入的收益分布。"""
    tr = transition_events(df)
    if len(tr) == 0:
        return pd.DataFrame(), 0
    ev = tr[tr[event_mask_col]]
    fr = forward_returns(df["close"], horizons)
    rows = []
    for _, e in ev.iterrows():
        if e["date"] in fr.index:
            rows.append({**{f"fwd{h}": fr.loc[e["date"], f"fwd{h}"] for h in horizons}})
    obs = pd.DataFrame(rows)
    stats = []
    for h in horizons:
        s = obs[f"fwd{h}"].dropna()
        if len(s) == 0:
            continue
        stats.append(_stat_row("event", h, s))
    return pd.DataFrame(stats), len(ev)


def baseline_returns(df: pd.DataFrame, horizons=(20, 60, 120, 250)):
    """无条件基准：任意一天买入的收益分布。"""
    fr = forward_returns(df["close"], horizons)
    stats = []
    for h in horizons:
        s = fr[f"fwd{h}"].dropna()
        stats.append(_stat_row("baseline", h, s))
    return pd.DataFrame(stats)


# ---------------- zigzag 周期段统计 ----------------

def zigzag_cycle_stats(df: pd.DataFrame, pct=None, min_days=None):
    """历史周期段（描述性）：每轮上行的起点/终点/涨幅/历时。"""
    pct = pct or config.CYCLE_SPLIT["zigzag_pct"]
    min_days = min_days or config.CYCLE_SPLIT["min_cycle_days"]
    ups, downs = factors.cycle_segments(df["close"], pct=pct, min_days=min_days)
    return pd.DataFrame(ups), pd.DataFrame(downs)


# ---------------- 因子有效性（IC 简化版） ----------------

def factor_ic_analysis(df: pd.DataFrame, factor_cols=None, horizons=(20, 60)):
    """秩相关 IC：因子当日值 vs 未来收益（时序上逐日秩相关，只对单板块做简化版）。

    返回 {factor: {horizon: mean_ic, ic_ir}}
    """
    factor_cols = factor_cols or ["mom60", "rs60", "pb_pct", "breadth", "price_pos", "temp"]
    fr = forward_returns(df["close"], horizons)
    res = {}
    for f in factor_cols:
        if f not in df.columns:
            continue
        for h in horizons:
            x = df[f]
            y = fr[f"fwd{h}"]
            m = x.notna() & y.notna()
            if m.sum() < 250:
                continue
            # 滚动秩相关（按 120 日窗口分组计算避免逐日噪声）
            ic_series = _rolling_spearman(x[m], y[m], window=120)
            if ic_series is None or len(ic_series.dropna()) < 10:
                continue
            res.setdefault(f, {})[h] = {
                "mean_ic": float(ic_series.mean()),
                "ic_ir": float(ic_series.mean() / ic_series.std()) if ic_series.std() > 0 else 0.0,
            }
    return res


def pooled_factor_ic(dfs: dict, factor_cols=None, horizons=(20, 60)):
    """跨板块平均的时序秩 IC —— 评估各因子对周期股未来收益的预测力。"""
    acc = {}
    for name, df in dfs.items():
        try:
            r = factor_ic_analysis(df, factor_cols=factor_cols, horizons=horizons)
        except Exception:
            continue
        for f, hd in r.items():
            for h, v in hd.items():
                acc.setdefault((f, h), []).append(v["mean_ic"])
    rows = []
    for (f, h), vals in sorted(acc.items()):
        rows.append({
            "factor": f, "horizon": h, "n_sectors": len(vals),
            "mean_ic": float(np.mean(vals)),
            "ic_ir": float(np.mean(vals) / (np.std(vals) / max(np.sqrt(len(vals)), 1))),
        })
    return pd.DataFrame(rows)


def _rolling_spearman(x: pd.Series, y: pd.Series, window=120) -> pd.Series:
    try:
        from scipy.stats import spearmanr
    except ImportError:
        return None
    ics = {}
    arr_x, arr_y = x.values, y.values
    idx = x.index
    for i in range(window, len(arr_x), window // 4):
        xs, ys = arr_x[i - window:i], arr_y[i - window:i]
        if np.std(xs) == 0 or np.std(ys) == 0:
            continue
        ics[idx[i]] = spearmanr(xs, ys)[0]
    return pd.Series(ics)


# ---------------- 商品领先指标 ----------------

def commodity_lead_lag(sector_close: pd.Series, fut_close: pd.Series,
                       lags=(0, 1, 2, 3), freq=20):
    """板块月频收益 vs 商品月频收益的滞后互相关（周期级别, 日频噪声太大）。

    corr(sec_ret[t], fut_ret[t - lag*freq]) > 0 且随 lag 增大 → 商品领先板块。
    返回 {lag*freq: corr}
    """
    sec_ret = sector_close.pct_change(freq)
    fut_ret = fut_close.pct_change(freq)
    out = {}
    for lag in lags:
        try:
            out[lag * freq] = float(sec_ret.corr(fut_ret.shift(lag * freq)))
        except Exception:
            out[lag * freq] = np.nan
    return out


def commodity_panel(dfs: dict, fut_map: dict, us_map: dict = None):
    """汇总各板块的商品/美股代理领先指标。

    fut_map: {板块: 期货close DataFrame(data层)}
    us_map: {板块: 美股close DataFrame}
    返回 DataFrame(板块, 商品, 当前商品60日动量, 领先滞后相关性...)
    """
    rows = []
    for name, df in dfs.items():
        lead = None
        lead_name = ""
        mom = np.nan
        if name in fut_map and fut_map[name] is not None and len(fut_map[name]) > 300:
            fdf = fut_map[name].set_index("date")
            lead = commodity_lead_lag(df["close"], fdf["close"])
            lead_name = "期货"
            mom = fdf["close"].pct_change(60).iloc[-1]
        elif us_map and name in (us_map or {}) and us_map[name] is not None and len(us_map[name]) > 300:
            udf = us_map[name].set_index("date")
            lead = commodity_lead_lag(df["close"], udf["close"])
            lead_name = "美股代理"
            mom = udf["close"].pct_change(60).iloc[-1]
        if lead is None:
            continue
        best_lag = max(lead, key=lambda k: lead[k] if np.isfinite(lead[k]) else -9)
        rows.append({
            "sector": name,
            "lead_type": lead_name,
            "corr_lag0": lead.get(0, np.nan),
            "corr_lag20": lead.get(20, np.nan),
            "corr_lag40": lead.get(40, np.nan),
            "corr_lag60": lead.get(60, np.nan),
            "best_lag": best_lag,
            "best_corr": lead.get(best_lag, np.nan),
            "lead_mom60": mom,
        })
    return pd.DataFrame(rows)


# ---------------- 跨板块汇总 ----------------

def pooled_stage_table(dfs: dict, horizons=(20, 60, 120, 250), level="episode"):
    """汇总所有板块的阶段-收益表。dfs: {板块名: enriched DataFrame}"""
    tables = []
    for name, df in dfs.items():
        t = stage_return_table(df, horizons=horizons, level=level)
        if len(t):
            t["sector"] = name
            tables.append(t)
    if not tables:
        return pd.DataFrame()
    return pd.concat(tables, ignore_index=True)


def pooled_event_study(dfs: dict, event_col="is_cycle_start", horizons=(20, 60, 120, 250)):
    """跨板块事件研究：所有板块的启动（或见顶）事件日买入的收益分布。"""
    rows = []
    for name, df in dfs.items():
        tr = transition_events(df)
        if len(tr) == 0:
            continue
        ev = tr[tr[event_col]]
        fr = forward_returns(df["close"], horizons)
        for _, e in ev.iterrows():
            if e["date"] in fr.index:
                rows.append({"sector": name, "date": e["date"],
                             **{f"fwd{h}": fr.loc[e["date"], f"fwd{h}"] for h in horizons}})
    obs = pd.DataFrame(rows)
    stats = []
    for h in horizons:
        s = obs[f"fwd{h}"].dropna()
        if len(s) == 0:
            continue
        stats.append(_stat_row(event_col, h, s))
    return pd.DataFrame(stats), obs


def pooled_baseline(dfs: dict, horizons=(20, 60, 120, 250)):
    """跨板块无条件基准：任意板块任意一天买入。"""
    frames = []
    for name, df in dfs.items():
        fr = forward_returns(df["close"], horizons)
        fr = fr[df["stage"] != "warmup"]
        frames.append(fr)
    allx = pd.concat(frames)
    stats = []
    for h in horizons:
        s = allx[f"fwd{h}"].dropna()
        stats.append(_stat_row("baseline", h, s))
    return pd.DataFrame(stats)


def pooled_summary(pooled: pd.DataFrame, horizons=(20, 60, 120, 250)):
    """跨板块按阶段聚合（板块内先等权平均，再跨板块统计）。"""
    rows = []
    for stage in ["decline", "bottom", "recovery", "expansion", "topping"]:
        sub = pooled[pooled["stage"] == stage]
        if len(sub) == 0:
            continue
        for h in horizons:
            s = sub[sub["horizon"] == h]
            if len(s) == 0:
                continue
            rows.append({
                "stage": stage,
                "horizon": h,
                "n_sectors": s["sector"].nunique(),
                "avg_win_rate": float(s["win_rate"].mean()),
                "avg_mean_ret": float(s["mean_ret"].mean()),
                "avg_median_ret": float(s["median_ret"].mean()),
                "worst_sector_ret": float(s["mean_ret"].min()),
                "best_sector_ret": float(s["mean_ret"].max()),
            })
    return pd.DataFrame(rows)
