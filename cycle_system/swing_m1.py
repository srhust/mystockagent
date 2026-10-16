# -*- coding: utf-8 -*-
"""波段M-v1 (R14): 修复M-v0时序P0并接入统一执行器。

时序状态机(I1): T-1收盘冻结计划 → T日开盘执行 → T日收盘估值+新信号。负例保证收盘信号不得同日成交。
执行一致(I2): ashare_executor.create_plan/execute_plan + Ledger.apply(raw价/涨跌停/停牌/整手/费用/T+1);
  pool_at(T)资格门禁; 缺行情持仓按最近有效价估值(NAV不蒸发)。
行业指数(I3): 等权**收益**均值聚合(非价格均值)。
基线(I4): 同域/同日期/同费/同执行 — B0买入持有 B1行业MA20趋势 B2纯动量top5。
探索性披露: 幸存者偏差/ST不可得/停牌近似/行业当前快照/样本外未完成——不构成正式业绩。
"""
import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ashare_executor as ex
import ashare_stock_pipeline as sp
from ashare_tradeable_pool import pool_at

DATA = sp.DATA_DIR
COST_NOTE = "费用由executor统一模型(佣金/印花税/过户费)"


def load_panel() -> pd.DataFrame:
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


def industry_index(panel: pd.DataFrame) -> pd.DataFrame:
    """I3: 等权收益聚合的行业指数(基点统一, 消除名义价格影响)。"""
    ra = panel.pivot(index="date", columns="code", values="close_adj").pct_change()
    sec = panel.drop_duplicates("code").set_index("code")["sector"]
    idx = {}
    for sector, codes in sec.groupby(sec).groups.items():
        cols = [c for c in codes if c in ra.columns]
        idx[sector] = (1 + ra[cols].mean(axis=1).fillna(0)).cumprod()
    idx = pd.DataFrame(idx)
    return idx / idx.iloc[0]


def cycle_stage(idx: pd.DataFrame) -> pd.DataFrame:
    """四态: MA60位置+60日动量(行业等权收益指数)。"""
    ma60, mom60 = idx.rolling(60).mean(), idx.pct_change(60)
    out = {}
    for c in idx.columns:
        above, pos = idx[c] > ma60[c], mom60[c] > 0
        st = pd.Series("weak", index=idx.index)
        st[(~above) & (~pos)], st[(above) & (~pos)], st[(above) & pos] = "bottom", "recover", "expand"
        out[c] = st
    return pd.DataFrame(out)


def stock_rank(panel: pd.DataFrame, day) -> list:
    """三因子等权(复权口径): 20日动量+趋势稳定+流动性。仅用T日之前数据。"""
    d0 = pd.Timestamp(day)
    hist = panel[panel["date"] < d0]
    f = {}
    for code, g in hist.groupby("code"):
        g = g.tail(60)
        if len(g) < 60:
            continue
        f[code] = [g["close_adj"].iloc[-1] / g["close_adj"].iloc[-21] - 1,
                   -(g["close_adj"].iloc[-20:] / g["close_adj"].iloc[-20:].iloc[0]).std(),
                   g["amount"].tail(20).mean()]
    f = pd.DataFrame.from_dict(f, orient="index", columns=["mom", "stab", "liq"]).dropna()
    if f.empty:
        return []
    z = (f.rank(pct=True) - 0.5) * 2
    return list((z.sum(axis=1)).sort_values(ascending=False).index)


def run(strategy: str = "m1", n_hold: int = 5) -> dict:
    """统一回测状态机。strategy: m1(周期门禁+三因子) / bh(买入持有) / trend(行业MA20) / mom(纯动量)。"""
    panel = load_panel()
    idx = industry_index(panel)
    stages = cycle_stage(idx)
    sector_of = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()
    dates = [pd.Timestamp(x) for x in sorted(panel["date"].unique())]
    dix = {d: i for i, d in enumerate(dates)}  # J4: 交易日索引
    close_adj = panel.pivot(index="date", columns="code", values="close_adj")
    close_raw = panel.pivot(index="date", columns="code", values="close")
    open_raw = panel.pivot(index="date", columns="code", values="open_raw")
    last_px = {}  # 最近有效价(NAV估值, 缺行情不蒸发)
    ledger = ex.Ledger(f"swing_{strategy}", nav0=20000.0, root=os.path.join(tempfile.gettempdir(), f"m1_{strategy}"), test_mode=False)  # K2: 正式账本模式
    cash, pos, pending = ledger.cash, {}, []  # pending=[(code,action,signal_day)]
    nav_log, trades, rej = [], {"BUY": 0, "SELL": 0}, {"halted": 0, "limit": 0, "lot": 0, "no_px": 0, "pool": 0, "other": 0}
    month_seen = None
    hold_days, buy_day = [], {}
    reserved = [0.0]  # K2: 冻结中BUY预算(执行日释放)
    prev_tradeable = set()

    for i, day in enumerate(dates):
        day_str = str(day.date())
        ledger.advance_day(day_str)  # N3: 账本严格前向推进(先于apply)
        px_day = open_raw.loc[day] if day in open_raw.index else pd.Series(dtype=float)
        raw_day = close_raw.loc[day] if day in close_raw.index else pd.Series(dtype=float)
        # ── 1) 开盘执行T-1收盘冻结的完整计划(J1: 信号日已create+register, T日只execute) ──
        for plan in list(pending):
            code, action, sig_day = plan["code"], plan["action"], plan["trade_date"]
            px = px_day.get(code, np.nan)
            if px != px:
                rej["no_px"] += 1
                continue
            if action == "BUY":
                reserved[0] -= float(plan["cash_budget"])  # K2: 执行日释放预算(无论成交与否)
            try:
                halted = raw_day.get(code, np.nan) != raw_day.get(code, np.nan)
                pc = last_px.get(code)  # J2: 全市场前收盘(非当日开盘)
                fill = ex.execute_plan(plan, trade_date_t1=day_str, px_t1=px, prev_close=pc, halted=halted, is_st=False)
                if fill["status"] == "FILLED":
                    ledger.apply(fill)  # J3: 正式通道(register_plan已登记, 无allow_unplanned)
                    cash = ledger.cash
                    if action == "BUY":
                        pos[code] = pos.get(code, 0) + fill["shares"]
                        buy_day.setdefault(code, dix[day])
                        trades["BUY"] += 1
                    else:
                        pos[code] = pos.get(code, 0) - fill["shares"]
                        if pos[code] <= 0:
                            pos.pop(code)
                            hold_days.append(dix[day] - buy_day.pop(code, dix[day]))  # J4: 交易日计数
                        trades["SELL"] += 1
                else:
                    r = str(fill.get("reason", ""))  # K3: 按执行器真实reason分类
                    key = "limit" if ("涨停" in r or "跌停" in r or "limit" in r) else ("halted" if "停" in r else "reject_other")
                    rej[key] = rej.get(key, 0) + 1
            except Exception as _e:
                msg = str(_e)[:70]
                key = "insufficient_cash" if "现金透支" in msg else "other"  # K3: 现金透支独立分类
                rej[key] = rej.get(key, 0) + 1
                rej.setdefault("other_msgs", []).append(msg)
        pending = []
        # ── 2) 收盘估值+新信号(I1: 信号冻结至明日) ──
        for c, v in raw_day.dropna().items():  # J2: 全市场前收盘维护(非仅持仓)
            last_px[c] = float(v)
        for c in pos:  # 持仓以raw close为准
            v = raw_day.get(c, np.nan)
            if v == v:
                last_px[c] = float(v)
        nav = cash + sum(sh * last_px.get(c, 0.0) for c, sh in pos.items())
        nav_log.append({"date": day_str, "nav": round(nav, 2), "cash": round(cash, 2), "n_pos": len(pos)})
        # ── 3) 收盘信号→冻结完整计划(J1: 信号日create+register, T+1只execute; px_ref/股数/预算全用信号日收盘) ──
        def freeze(code, action, sig=day_str, sig_close=None):
            sigc = float(sig_close) if sig_close is not None else last_px.get(code)
            if sigc != sigc:
                rej["no_px"] += 1
                return
            if action == "BUY":
                base = 200 if code.startswith("68") else 100
                alloc = min((cash - reserved[0]) / max(1, n_hold), cash - reserved[0])  # K2: 扣除冻结中预算
                if alloc < sigc * base:  # K2: 预算不足不再超额申报
                    rej["insufficient_cash"] = rej.get("insufficient_cash", 0) + 1
                    return
                sh = int(alloc * 0.99 / sigc / base) * base
                if sh < base:
                    rej["lot"] += 1
                    return
                plan = ex.create_plan(code, "BUY", sig, px_ref=sigc, cash_budget=round(sh * sigc * 1.005, 2), created_at=sig + "T15:30:00+08:00")
                reserved[0] += float(plan["cash_budget"])  # K2: 冻结预算预留
            else:
                sh = pos.get(code, 0)
                if sh <= 0:
                    return
                plan = ex.create_plan(code, "SELL", sig, px_ref=sigc, shares=sh, created_at=sig + "T15:30:00+08:00")
            ledger.register_plan(plan)  # J3: 正式登记(计划身份校验启用)
            pending.append(plan)
        new_month = day.month != month_seen
        month_seen = day.month
        if strategy == "bh":
            if i == 65 and not pos and not pending:
                for c in stock_rank(panel, day)[:20]:
                    freeze(c, "BUY")
        elif strategy in ("m1", "mom"):
            if new_month and day in stages.index:
                good = set(stages.columns) if strategy == "mom" else {c for c in stages.columns if stages.loc[day, c] in ("recover", "expand")}
                try:
                    prev_tradeable = set(pool_at(day_str)["buckets"]["TRADEABLE"])
                except RuntimeError:
                    prev_tradeable = prev_tradeable  # 日历未知→沿用(fail-closed不猜)
                rank = [c for c in stock_rank(panel, day) if sector_of.get(c) in good and c in prev_tradeable]
                for c in rank[:n_hold]:
                    s20 = close_adj[c].loc[:day].tail(20)
                    if c not in pos and len(s20) >= 20 and close_adj[c].get(day, np.nan) > s20.mean():  # 趋势确认入场(收盘>MA20), 否则次日即破位翻转
                        freeze(c, "BUY")
        if strategy == "m1":  # K1: 趋势破坏退出每日检查(月度只控入场), 收盘信号T+1执行
            for c in list(pos):
                s = close_adj[c].loc[:day].tail(20)
                if len(s) >= 20 and close_adj[c].get(day, np.nan) < s.mean() * 0.97:  # 3%破位缓冲
                    freeze(c, "SELL")
        elif strategy == "trend":
            si = idx.mean(axis=1)
            ma20 = si.loc[:day].tail(20).mean()
            up = si.get(day, np.nan) > ma20
            if new_month:
                try:
                    prev_tradeable = set(pool_at(day_str)["buckets"]["TRADEABLE"])
                except RuntimeError:
                    pass
                rank = [c for c in stock_rank(panel, day) if c in prev_tradeable][:10]
                if up:
                    for c in rank[:n_hold]:
                        if c not in pos:
                            freeze(c, "BUY")
                else:
                    for c in list(pos):
                        freeze(c, "SELL")
    nav = pd.Series([x["nav"] for x in nav_log], index=[pd.Timestamp(x["date"]) for x in nav_log])
    ret = nav.iloc[-1] / 20000 - 1
    dd = (nav / nav.cummax() - 1).min()
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    ann = (1 + ret) ** (1 / yrs) - 1 if yrs > 0 else 0
    rstd = nav.pct_change().std()
    out = {"strategy": strategy,
           "caveats": {"survivorship": "仅当前配置池", "st_history": "UNAVAILABLE", "suspension": "行情近似",
                       "industry": "当前快照非历史时点", "oos": "样本外验证未完成, 探索性不构成正式业绩",
                       "executor": "统一执行器+账本(raw价/涨跌停/停牌/整手/T+1/真实费用)"},
           "stats": {"return": round(ret, 4), "ann": round(ann, 4), "max_dd": round(dd, 4),
                     "calmar": round(ann / abs(dd), 2) if dd < 0 else 0,
                     "sharpe": round(nav.pct_change().mean() / rstd * 252 ** 0.5, 2) if rstd > 0 else 0,
                     "trades": trades, "rejects": rej,
                     "cash_pct": round(np.mean([x["cash"] / x["nav"] for x in nav_log if x["nav"] > 0]), 3),
                     "avg_hold_days": int(np.mean(hold_days)) if hold_days else None,
                     "n_days": len(nav)}}
    os.makedirs(r"E:\mystockagent\output\ashare", exist_ok=True)
    seg = {}  # 滚动样本外近似: 前段构建参考/后段更晚数据(非严格OOS, 如实标注)
    for a, b, tag in [("2020-01-01", "2022-12-31", "seg_2020_2022"), ("2023-01-01", "2026-12-31", "seg_2023_2026")]:
        s2 = nav[(nav.index >= a) & (nav.index <= b)]
        if len(s2) > 10:
            seg[tag] = {"return": round(s2.iloc[-1] / s2.iloc[0] - 1, 4),
                        "max_dd": round((s2 / s2.cummax() - 1).min(), 4)}
    out["segments"] = seg
    json.dump(out, open(rf"E:\mystockagent\output\ashare\swing_m1_{strategy}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


if __name__ == "__main__":
    res = {s: run(s) for s in ("m1", "bh", "trend", "mom")}
    cmp = {s: r["stats"] for s, r in res.items()}
    json.dump({"caveats": res["m1"]["caveats"], "compare": cmp},
              open(r"E:\mystockagent\output\ashare\swing_m1_compare.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(cmp, ensure_ascii=False))
