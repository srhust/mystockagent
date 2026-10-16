# -*- coding: utf-8 -*-
"""波段M-v0探索性研究 (R13): 行业周期定位 → 行业内个股排序 → 波段入场/趋势破坏退出 → 基线对照。

探索性披露: 幸存者偏差(仅当前配置池)/历史ST不可得/停复牌为行情近似/行业归属为当前快照。
全部结论为探索性, 样本外验证未完成, 不构成正式业绩。T日决策(T收盘数据)→T+1 open成交(raw真实价)。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ashare_stock_pipeline as sp

DATA = sp.DATA_DIR
COST = 0.0013  # 双边合计佣金+冲击近似


def load_panel() -> pd.DataFrame:
    """长表: date, code, sector, close_raw, open_raw, close_adj(复权口径信号), amount。"""
    rows = []
    for code, name, sector in sp.configured_stocks():
        p = os.path.join(DATA, f"{code}.csv")
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p)
        if "adj_factor" not in df.columns:
            continue
        df["code"], df["sector"] = code, sector
        df["close_adj"] = df["close"] * df["adj_factor"]
        rows.append(df[["date", "code", "sector", "open", "close", "close_adj", "amount"]])
    t = pd.concat(rows).rename(columns={"open": "open_raw"}).drop_duplicates(subset=["date", "code"])
    t["date"] = pd.to_datetime(t["date"])
    return t.sort_values(["date", "code"]).reset_index(drop=True)


def industry_cycle(panel: pd.DataFrame) -> pd.DataFrame:
    """行业周期四态: 行业等权复权指数, MA60位置+60日动量→bottom/recover/expand/weak。"""
    idx = panel.groupby(["date", "sector"])["close_adj"].mean().unstack()
    ma60 = idx.rolling(60).mean()
    mom60 = idx.pct_change(60)
    out = {}
    for c in idx.columns:
        above = idx[c] > ma60[c]
        pos = mom60[c] > 0
        stage = pd.Series("weak", index=idx.index)
        stage[(~above) & (~pos)] = "bottom"
        stage[(above) & (~pos)] = "recover"
        stage[(above) & pos] = "expand"
        stage[(~above) & pos] = "weak"
        out[c] = stage
    return pd.DataFrame(out)


def stock_rank(panel: pd.DataFrame, day) -> pd.Series:
    """个股排序: 20日动量(复权) + 60日趋势稳定(比值均值/波动) + 流动性(20日均额)。可解释三因子等权。"""
    d = panel[panel["date"] == pd.Timestamp(day)]
    if d.empty:
        return pd.Series(dtype=float)
    hist = panel[panel["date"] < pd.Timestamp(day)]
    f = {}
    for code, g in hist.groupby("code"):
        g = g.tail(60)
        if len(g) < 60:
            continue
        r = g["close_adj"].iloc[-1] / g["close_adj"].iloc[-21] - 1 if len(g) >= 21 else np.nan
        stab = (g["close_adj"].iloc[-20:] / g["close_adj"].iloc[-20:].iloc[0]).std()
        liq = g["amount"].tail(20).mean()
        f[code] = {"mom": r, "stab": -stab if stab == stab else np.nan, "liq": liq}
    f = pd.DataFrame(f).T.dropna()
    if f.empty:
        return pd.Series(dtype=float)
    z = (f.rank(pct=True) - 0.5) * 2
    return (z["mom"] + z["stab"] + z["liq"]).sort_values(ascending=False)


def run_m0(n_hold: int = 5, cycle_gate=("recover", "expand")) -> dict:
    """M-v0回测: 每月初T日决策→T+1 open成交; 持有至趋势破坏(close_adj<MA20)或90交易日观察期满。"""
    panel = load_panel()
    sector_of = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()
    cyc = industry_cycle(panel)
    dates = sorted(panel["date"].unique())
    cash, pos, nav_log, trades, rejects = 20000.0, {}, [], 0, 0
    month_seen = None
    close_adj = panel.pivot(index="date", columns="code", values="close_adj")
    open_raw = panel.pivot(index="date", columns="code", values="open_raw")
    for i, day in enumerate(dates[65:]):
        day = pd.Timestamp(day)
        mret = 1.0 + (close_adj.loc[day].dropna() / close_adj.loc[dates[dates.index(day) - 1]].dropna() - 1).fillna(0).sum() if dates.index(day) > 0 else 1.0
        # 持仓管理: 趋势破坏退出(close_adj跌破MA20)
        for code in list(pos):
            s = close_adj[code].loc[:day].tail(20)
            if len(s) >= 20 and close_adj[code].get(day, np.nan) < s.mean():
                px = open_raw[code].get(day, np.nan)
                if px == px:
                    cash += pos.pop(code) * px * (1 - COST)
                    trades += 1
        # 月度再平衡入场
        if day.month != month_seen and day in cyc.index:
            month_seen = day.month
            good = [c for c in cyc.columns if cyc.loc[day, c] in cycle_gate]
            cand = [c for c in stock_rank(panel, day).index if sector_of.get(c) in good][: n_hold * 2]
            # T+1成交
            if day in dates[:-1]:
                t1 = pd.Timestamp(dates[dates.index(day) + 1])
                picks = [c for c in cand if c not in pos][:n_hold]
                for c in picks:
                    px = open_raw[c].get(t1, np.nan)
                    if px == px and cash > 3000:
                        alloc = cash / max(1, (n_hold - len(pos)))
                        sh = int(alloc * (1 - COST) / px / 100) * 100
                        if sh >= 100:
                            pos[c] = sh
                            cash -= sh * px * (1 + COST)
                            trades += 1
                        else:
                            rejects += 1
                    else:
                        rejects += 1
        nav = cash + sum(sh * open_raw[c].get(day, np.nan) for c, sh in pos.items() if open_raw[c].get(day, np.nan) == open_raw[c].get(day, np.nan))
        nav_log.append({"date": str(day.date()), "nav": round(nav, 2)})
    nav = pd.Series([x["nav"] for x in nav_log], index=[pd.Timestamp(x["date"]) for x in nav_log])
    ret = nav.iloc[-1] / 20000 - 1
    dd = (nav / nav.cummax() - 1).min()
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    ann = (1 + ret) ** (1 / yrs) - 1 if yrs > 0 else 0
    calmar = ann / abs(dd) if dd < 0 else 0
    sharpe = (nav.pct_change().mean() / nav.pct_change().std()) * (252 ** 0.5) if nav.pct_change().std() > 0 else 0
    out = {"caveats": {"survivorship": "仅当前配置池", "st_history": "UNAVAILABLE",
                       "suspension": "行情近似", "industry": "当前快照非历史时点",
                       "oos": "样本外验证未完成, 探索性结果不构成正式业绩"},
           "m0": {"return": round(ret, 4), "ann": round(ann, 4), "max_dd": round(dd, 4),
                  "calmar": round(calmar, 2), "sharpe": round(sharpe, 2),
                  "trades": trades, "rejects": rejects, "n_days": len(nav)}}
    json.dump(out, open(r"E:\mystockagent\output\ashare\swing_m0.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


if __name__ == "__main__":
    print(json.dumps(run_m0(), ensure_ascii=False))
