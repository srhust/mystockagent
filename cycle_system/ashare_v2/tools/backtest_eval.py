# -*- coding: utf-8 -*-
"""backtest_eval — A线历史回测评估分析器(人类裁定2026-10-19)。

读 output/ashare_v2/long_backtest/ 产物(全历史REPLAY, 冻结参数零改动), 产出:
  ①总收益/CAGR/最大回撤(深度+持续)/年化波动/资金利用率 ②分年度 vs 等权基准
  ③LC/FV单独贡献(attribution口径, 含未平仓浮盈) ④行业集中度(成交+持仓HHI)
  ⑤不同市场阶段(基准季度动量分组)策略相对表现 ⑥Q2 STRICT问题定量拆解。

纪律: 全部为 REPLAY/RESEARCH 读数——历史回放, 非前向样本, 非收益宣称; STRICT/FORMAL
口径与之严格分离, 本工具不据此声称预测有效。
"""
from __future__ import annotations

import io
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                   # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                              # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                   # noqa: E402

RUN = os.path.join(C.OUTPUT_DIR, "long_backtest")
OUT = os.path.join(C.OUTPUT_DIR, "independent")


def _csv(name):
    p = os.path.join(RUN, name)
    df = pd.read_csv(p, encoding="utf-8-sig", dtype={"code": str})
    if "code" in df.columns:
        df["code"] = df["code"].str.zfill(6)
    return df


def overall(nav: pd.DataFrame) -> dict:
    n = nav["nav"].astype(float)
    days = (pd.to_datetime(nav["day"]).iloc[-1] - pd.to_datetime(nav["day"]).iloc[0]).days
    total = n.iloc[-1] / n.iloc[0] - 1
    cagr = (n.iloc[-1] / n.iloc[0]) ** (365.25 / max(days, 1)) - 1 if n.iloc[0] > 0 else None
    peak = n.cummax()
    dd = n / peak - 1
    i = int(dd.idxmin())
    trough_pos = nav.index.get_loc(i)
    peak_pos = nav.index.get_loc(n.loc[:i].idxmax())
    rec = n.loc[i:].reset_index(drop=True)
    back_above = rec[rec >= n.loc[i] * (1 + 0)].index
    recover_days = int(back_above[0]) if len(back_above) else None
    r = n.pct_change().dropna()
    util = 1 - nav["cash"].astype(float) / n
    return {"window_days": int(days), "trading_days": int(len(nav)),
            "nav_start": float(n.iloc[0]), "nav_end": float(n.iloc[-1]),
            "total_return": round(float(total), 4),
            "cagr": round(float(cagr), 4) if cagr is not None else None,
            "max_drawdown": round(float(dd.min()), 4),
            "max_dd_peak_day": str(nav["day"].iloc[peak_pos]), "max_dd_trough_day": str(nav["day"].iloc[i]),
            "max_dd_dur_days": int(trough_pos - peak_pos),
            "max_dd_recover_days": recover_days,
            "ann_vol": round(float(r.std() * np.sqrt(244)), 4),
            "sharpe_rf0": round(float(r.mean() / r.std() * np.sqrt(244)), 3) if r.std() > 0 else None,
            "worst_day": round(float(r.min()), 4), "best_day": round(float(r.max()), 4),
            "capital_util_avg": round(float(util.mean()), 4),
            "capital_util_p50": round(float(util.median()), 4),
            "cash_only_days_pct": round(float((util < 0.05).mean()), 4)}


def yearly(nav: pd.DataFrame, bench: pd.Series) -> list:
    out = []
    nav = nav.copy()
    nav["dt"] = pd.to_datetime(nav["day"])
    bench = bench.copy()
    bench.index = pd.to_datetime(bench.index)
    for y, g in nav.groupby(nav["dt"].dt.year):
        g = g.sort_values("dt")
        s, e = g["nav"].iloc[0], g["nav"].iloc[-1]
        b = bench[bench.index.year == y]
        bret = float(b.iloc[-1] / b.iloc[0] - 1) if len(b) > 2 else None
        gg = g["nav"].astype(float)
        ddy = float((gg / gg.cummax() - 1).min())
        out.append({"year": int(y), "days": int(len(g)), "strategy": round(float(e / s - 1), 4),
                    "benchmark_eq": round(bret, 4) if bret is not None else None,
                    "excess": round(float(e / s - 1) - bret, 4) if bret is not None else None,
                    "max_dd_in_year": round(ddy, 4)})
    return out


def engines(attribution: pd.DataFrame, trades: pd.DataFrame) -> dict:
    rows = {}
    for _, r in attribution.iterrows():
        eng = str(r.iloc[0])
        rows[eng] = {"realized": float(r["realized_total(模拟账本读数,非收益宣称)"]),
                     "sell_events": int(r["n_sell_events"]),
                     "open_positions": int(r["open_positions"]),
                     "open_shares": int(r["open_shares"]),
                     "unrealized": float(r["unrealized(按last-known价)"])}
        rows[eng]["total_engine_pnl"] = round(rows[eng]["realized"] + rows[eng]["unrealized"], 2)
    if "engine_attribution" in trades.columns and trades["engine_attribution"].notna().any() \
            and (trades["engine_attribution"].astype(str).str.len() > 0).any():
        ea = trades[trades["engine_attribution"].astype(str).str.len() > 0]
        rows["_trade_engine_tag_coverage"] = round(float(len(ea) / max(len(trades), 1)), 3)
        rows["_trades_by_engine"] = ea.groupby("engine_attribution")["code"].count().to_dict()
    return rows


def sectors(trades: pd.DataFrame, nav: pd.DataFrame, code2sec: dict) -> dict:
    t = trades.copy()
    t["sector"] = t["code"].map(code2sec).fillna("__UNKNOWN__")
    by_sec = t.groupby("sector")["notional"].agg(["count", "sum"]).sort_values("sum", ascending=False)
    tot = float(by_sec["sum"].sum())
    top = [{"sector": s, "trades": int(r["count"]), "notional_share": round(float(r["sum"]) / tot, 4)}
           for s, r in by_sec.head(10).iterrows()]
    hhi_trade = float(sum((r["sum"] / tot) ** 2 for _, r in by_sec.iterrows()))
    # 持仓端: 抽样每日 top1 行业权重
    tops, hhis = [], []
    for _, r in nav.iterrows():
        try:
            pos = json.loads(r["positions_json"]) if isinstance(r["positions_json"], str) else {}
        except (TypeError, json.JSONDecodeError):
            continue
        pos = {k: v for k, v in (pos or {}).items() if v}
        if not pos:
            continue
        w = pd.Series({k: float(v) for k, v in pos.items()})
        secw = w.groupby(w.index.map(lambda c: code2sec.get(c, "__UNKNOWN__"))).sum()
        secw = secw / secw.sum()
        tops.append(float(secw.max()))
        hhis.append(float((secw ** 2).sum()))
    return {"traded_sectors_n": int(len(by_sec)), "trade_hhi": round(hhi_trade, 3),
            "top10": top,
            "pos_top1_sector_weight_avg": round(float(np.mean(tops)), 3) if tops else None,
            "pos_top1_sector_weight_p90": round(float(np.percentile(tops, 90)), 3) if tops else None,
            "pos_hhi_avg": round(float(np.mean(hhis)), 3) if hhis else None}


def regimes(nav: pd.DataFrame, bench: pd.Series) -> dict:
    n = nav.copy()
    n["dt"] = pd.to_datetime(n["day"])
    n["q"] = n["dt"].dt.to_period("Q")
    b = bench.copy()
    b.index = pd.to_datetime(b.index)
    bq = b.resample("QE").last().pct_change(periods=4).dropna()   # 同比动量定市场阶段
    stage = {}
    for ts, mom in bq.items():
        lab = "上行" if mom > 0.10 else ("下行" if mom < -0.10 else "震荡")
        stage[str(ts.to_period("Q"))] = lab
    rows = []
    for q, g in n.groupby("q"):
        lab = stage.get(str(q), "未分类")
        s, e = float(g["nav"].iloc[0]), float(g["nav"].iloc[-1])
        try:
            b0 = b[b.index < g["dt"].min() - pd.Timedelta(days=3)]
            b_q = float(b.loc[g["dt"].max()] / b0.iloc[-1] - 1) if len(b0) else None
        except KeyError:
            b_q = None
        rows.append({"quarter": str(q), "stage": lab, "strategy": round(e / s - 1, 4),
                     "benchmark": round(b_q, 4) if b_q is not None else None})
    df = pd.DataFrame(rows)
    grp = {}
    for lab, g in df[df["stage"] != "未分类"].groupby("stage"):
        grp[lab] = {"n_quarters": int(len(g)),
                    "strategy_avg": round(float(g["strategy"].mean()), 4),
                    "strategy_win_q": round(float((g["strategy"] > 0).mean()), 3),
                    "benchmark_avg": round(float(pd.Series([x for x in g["benchmark"] if x is not None]).mean()), 4)}
    return {"per_stage": grp, "n_quarters": int(len(df))}


def strict_split(opps: pd.DataFrame, alloc: pd.DataFrame) -> dict:
    """Q2定量: STRICT=0源于数据审计闸门(G1-G4, 与信号质量无关); 研究信号质量另列读数。"""
    cred = opps["data_credibility"].fillna("NA").value_counts().to_dict() if "data_credibility" in opps else {}
    trust = opps["membership_trust"].fillna("NA").value_counts().to_dict() if "membership_trust" in opps else {}
    sp = pd.to_numeric(opps["score_points"], errors="coerce").dropna()
    buys = int(alloc["n_buys"].sum())
    sells = int(alloc["n_sells"].sum())
    return {"gates_root_cause": {
                "G1_SW_PARTIAL": "申万成员无out_date→行业归属不可PIT验证→行业级正式信号阻断",
                "G2_MARKET_WIDE_POOL": "全市场历史池未落盘→全域选股DATA_BLOCKED",
                "G3_ST_FLAG": "历史ST标记缺失→is_st未验证不得正式成交",
                "G4_FUNDAMENTAL_TS": "指标发布时间缺失→asof不可证→正式信号拒绝",
                "性质": "四闸门均为数据溯源/审计条件, 与信号质量阈值无关; 信号质量好也不会放行"},
            "research_signal_quality": {
                "candidate_days": int(len(opps)), "candidates_per_day_avg": round(float(len(opps) / max(1, opps["day"].nunique())), 2),
                "score_p50": round(float(sp.median()), 2) if len(sp) else None,
                "score_p90": round(float(sp.quantile(0.9)), 2) if len(sp) else None,
                "score_max": round(float(sp.max()), 2) if len(sp) else None,
                "credibility_dist": {k: int(v) for k, v in cred.items()},
                "trust_dist": {k: int(v) for k, v in trust.items()},
                "planned_buys_total": buys, "planned_sells_total": sells}}


def main() -> int:
    portal = DataPortal(data_mode=C.PIT_RESEARCH)
    panel = portal.price_panel()
    code2sec = {str(k).zfill(6): v for k, v in
                panel.drop_duplicates("code").set_index("code")["sector"].to_dict().items()}
    px = panel.pivot(index="date", columns="code", values="close_adj")
    bmean = px.pct_change(fill_method=None).mean(axis=1).dropna()
    bench = (1 + bmean).cumprod()
    nav = _csv("nav_curve.csv")
    res = {
        "_disclaimer": "REPLAY/RESEARCH历史回放读数: 非前向样本, 非收益宣称; STRICT/FORMAL口径独立且仍阻断。"
                       " 样本=冻结R31当前池(幸存者偏差部分标注SURVIVORSHIP_BIAS_PARTIAL)。",
        "overall": overall(nav),
        "by_year": yearly(nav, bench),
        "engine_contribution": engines(_csv("attribution.csv"), _csv("trades.csv")),
        "sector_concentration": sectors(_csv("trades.csv"), nav, code2sec),
        "market_regimes": regimes(nav, bench),
        "q2_strict_decomposition": strict_split(_csv("opportunities.csv"), _csv("allocation_plan_log.csv")),
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "backtest_eval.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
