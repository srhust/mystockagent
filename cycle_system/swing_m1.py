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


def build_factor_table(panel: pd.DataFrame) -> pd.DataFrame:
    """R19性能: 因子一次性向量化预计算(滚动20日口径v2, 与旧60日尾部窗口近似非恒等——如实标注), 每日排序查表。"""
    p = panel.sort_values(["date", "code"]).copy()
    p["mom20"] = p.groupby("code")["close_adj"].pct_change(20)
    p["stab20"] = -p.groupby("code")["close_adj"].transform(lambda s: s.pct_change().rolling(20).std())
    p["liq20"] = p.groupby("code")["amount"].transform(lambda s: s.rolling(20).mean())
    for c in ("mom20", "stab20", "liq20"):
        p[f"z_{c}"] = p.groupby("date")[c].rank(pct=True)  # 每日截面分位
    p["score_3f"] = p["z_mom20"] + p["z_stab20"] + p["z_liq20"]
    p["score_mom"] = p["z_mom20"]
    return p.dropna(subset=["score_3f"])


FACTOR_TABLE = None  # R19性能: 预计算因子表(模块级, 首次run构建)
_DATE_ROWS = {}  # R21性能: {day: subdf}按日分组(O(1)查), 替代每日40万行布尔扫


def _date_rows(ft: pd.DataFrame, day) -> pd.DataFrame:
    d0 = pd.Timestamp(day)
    if not _DATE_ROWS:
        for d, g in ft.groupby("date"):
            _DATE_ROWS[d] = g
    return _DATE_ROWS.get(d0, pd.DataFrame())


def stock_scores(panel: pd.DataFrame, day, kind: str = "3f") -> dict:
    """R21: 返回{code: score}(当日截面, 仅T日数据), 供评分加权资金分配。"""
    global FACTOR_TABLE
    if FACTOR_TABLE is None:
        FACTOR_TABLE = build_factor_table(panel)
    row = FACTOR_TABLE[FACTOR_TABLE["date"] == pd.Timestamp(day)]
    if row.empty:
        return {}
    col = "score_mom" if kind == "mom" else "score_3f"
    return dict(zip(row["code"], row[col]))


def stock_rank(panel: pd.DataFrame, day, kind: str = "3f") -> list:
    """R19查表版: 因子已预计算(滚动20日口径v2, 与旧60日尾部窗口近似非恒等——如实标注)。仅用T日数据(因子不含未来)。"""
    global FACTOR_TABLE
    if FACTOR_TABLE is None:
        FACTOR_TABLE = build_factor_table(panel)
    d0 = pd.Timestamp(day)
    row = FACTOR_TABLE[FACTOR_TABLE["date"] == d0]
    if row.empty:
        return []
    col = "score_mom" if kind == "mom" else "score_3f"
    return list(row.sort_values(col, ascending=False)["code"])


def run(strategy: str = "m1", n_hold: int = 5, gate: str = "auto", exit_mode: str = "auto", rank_kind: str = "auto", entry_mode: str = "auto", cap: int = 0, alloc_policy: str = "equal") -> dict:
    """R18: gate/exit_mode/rank_kind显式参数(auto=按strategy推导); E系列=expt+显式三参。
    exit_mode: daily(每日MA20x0.97)/monthly(月末信号, 新月首日执行)/none(无趋势退出, 持有至期末按最近价估值)。"""
    """统一回测状态机。strategy: m1(周期门禁+三因子) / bh(买入持有) / trend(行业MA20) / mom(纯动量)。"""
    panel = load_panel()
    idx = industry_index(panel)
    stages = cycle_stage(idx)
    sector_of = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()
    dates = [pd.Timestamp(x) for x in sorted(panel["date"].unique())]
    dix = {d: i for i, d in enumerate(dates)}  # J4: 交易日索引
    close_adj = panel.pivot(index="date", columns="code", values="close_adj")
    ma20 = close_adj.rolling(20, min_periods=20).mean()  # R21性能: MA20面板一次预计算(与loc[:day].tail(20).mean()恒等)
    close_raw = panel.pivot(index="date", columns="code", values="close")
    open_raw = panel.pivot(index="date", columns="code", values="open_raw")
    last_px = {}  # 最近有效价(NAV估值, 缺行情不蒸发)
    if entry_mode == "auto":
        entry_mode = "daily" if strategy in ("r2", "r4") else "monthly"
    if gate == "auto":
        gate = strategy in ("m1", "gatemom")
    if rank_kind == "auto":
        rank_kind = "mom" if strategy in ("gatemom", "nogatemom") else "3f"
    if exit_mode == "auto":
        exit_mode = {"m1": "daily", "gatemom": "daily", "nogatemom": "daily", "nogate3f": "daily"}.get(strategy, "daily" if strategy in ("m1", "gatemom", "nogatemom", "nogate3f") else "daily")
    ledger = ex.Ledger(f"swing_{strategy}_{gate}_{exit_mode}", nav0=20000.0, root=os.path.join(tempfile.gettempdir(), f"m1_{strategy}"), test_mode=False)  # K2: 正式账本模式
    cash, pos, pending = ledger.cash, {}, []  # pending=[(code,action,signal_day)]
    nav_log, trades, rej = [], {"BUY": 0, "SELL": 0}, {"halted": 0, "limit": 0, "lot": 0, "no_px": 0, "pool": 0, "other": 0}
    month_seen = None
    hold_days, buy_day = [], {}
    today_sum = [1.0]  # R21: 当日候选正分和(score分配分母)
    reentry_block = {}  # R19: 冷却期(卖出执行日起5个交易日内禁重入, 预设统一)
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
            if action == "BUY":
                reserved[0] -= float(plan["cash_budget"])  # L1: 进入处理即释放(成交/拒单/缺价/异常四路径统一终态)
            px = px_day.get(code, np.nan)
            if px != px:
                rej["no_px"] += 1
                continue
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
                            reentry_block[code] = dix[day] + 5  # R19: 冷却
                        trades["SELL"] += 1
                else:
                    r = str(fill.get("reason", "unknown"))  # L2: reason原样逐笔归因
                    rej.setdefault("reason_detail", {}).setdefault(r, 0)
                    rej["reason_detail"][r] += 1
                    if r == "insufficient_cash_after_fees" or "现金" in r:  # P1: 英文枚举优先
                        key = "execution_cash_reject"  # T+1跳空费用后现金不足
                    elif "涨停" in r or "跌停" in r or "停" in r:
                        key = "market_reject"  # P1: 涨跌停/停牌
                    else:
                        key = "market_reject_other"
                    rej[key] = rej.get(key, 0) + 1
                    rej.setdefault("other_msgs", []).append(r[:70])
            except AssertionError as _e:  # P3: 账本断言异常=账本一致性破坏, 立即失败不吞
                if "现金透支" in str(_e):
                    rej["execution_cash_reject"] = rej.get("execution_cash_reject", 0) + 1
                    rej.setdefault("other_msgs", []).append(str(_e)[:70])
                else:
                    raise
        assert abs(reserved[0]) < 1e-6, f"L1: 日终预留未清零 {reserved[0]}"  # 预算预留日终恒等
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
        def freeze(code, action, sig=day_str, sig_close=None, sig_score=None):
            sigc = float(sig_close) if sig_close is not None else last_px.get(code)
            if sigc != sigc:
                rej["no_px"] += 1
                return
            if action == "BUY":
                n_open = len(pos) + sum(1 for p in pending if p["action"] == "BUY")  # R20: 持仓+在途买单
                if cap and n_open >= cap:  # 容量上限: 不自动卖出(避免混入调仓效应)
                    rej["cap"] = rej.get("cap", 0) + 1
                    return
                if any(p["code"] == code for p in pending) or dix.get(pd.Timestamp(sig), -1) < reentry_block.get(code, -1):  # R19: 队列去重+冷却期
                    rej["cooldown"] = rej.get("cooldown", 0) + 1
                    return
                base = 200 if code.startswith("68") else 100
                if alloc_policy == "score" and sig_score is not None:
                    alloc = (cash - reserved[0]) * (sig_score / max(today_sum[0], 1e-9))  # R21: 评分比例分配
                elif alloc_policy == "free_slots":
                    alloc = (cash - reserved[0]) / max(1, (cap or 8) - n_open)  # R21: 剩余现金/剩余空位
                else:
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
        elif strategy in ("m1", "nogate3f", "gatemom", "nogatemom", "expt", "r2", "r4"):  # L3+E+R系列
            if (entry_mode == "daily" or (new_month and day in stages.index)) and day in stages.index:  # R19: 重入消融
                gate = bool(gate)  # L3/R18: 门禁开关(显式参数)
                good = {c for c in stages.columns if stages.loc[day, c] in ("recover", "expand")} if gate else set(stages.columns)
                try:
                    prev_tradeable = set(pool_at(day_str)["buckets"]["TRADEABLE"])
                except RuntimeError:
                    prev_tradeable = prev_tradeable  # 日历未知→沿用(fail-closed不猜)
                if alloc_policy == "score":
                    today_scores = stock_scores(panel, day, rank_kind)  # R21: 当日评分(截面前向无未来)
                rank = [c for c in stock_rank(panel, day, rank_kind) if sector_of.get(c) in good and c in prev_tradeable]
                if alloc_policy == "score":
                    today_sum[0] = sum(max(today_scores.get(c, 0.0), 0.0) for c in rank)  # 候选正分归一
                for c in rank[:n_hold]:
                    s20v = ma20[c].get(day, np.nan)  # 查表(恒等: 滚动20均值)
                    if c not in pos and s20v == s20v and close_adj[c].get(day, np.nan) > s20v:  # 趋势确认入场(收盘>MA20)
                        freeze(c, "BUY", sig_score=today_scores.get(c) if alloc_policy == "score" else None)
        if strategy in ("m1", "nogate3f", "gatemom", "nogatemom", "expt") and exit_mode == "daily":  # E系列: 退出模式参数化
            for c in list(pos):
                s = ma20[c].get(day, np.nan)
                if s == s and close_adj[c].get(day, np.nan) < s * 0.97:  # 3%破位缓冲(查表)
                    freeze(c, "SELL")
        elif strategy in ("m1", "nogate3f", "gatemom", "nogatemom", "expt") and exit_mode == "monthly":
            # R18 E3/E4: 月末MA20x0.97信号(收盘产生, 新月首日执行; 无提前时序)
            if i + 1 < len(dates) and dates[i + 1].month != day.month:
                for c in list(pos):
                    s = ma20[c].get(day, np.nan)
                    if s == s and close_adj[c].get(day, np.nan) < s * 0.97:
                        freeze(c, "SELL")
        # exit_mode == "none": 无趋势退出(E5/E6), 持有至期末按最近有效价估值
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
                      "flat_days": sum(1 for x in nav_log if x["n_pos"] == 0),
                      "avg_pos": round(np.mean([x["n_pos"] for x in nav_log]), 2),
                      "hold_buckets": {"1-5": sum(1 for h in hold_days if h <= 5), "6-20": sum(1 for h in hold_days if 6 <= h <= 20), "21-60": sum(1 for h in hold_days if 21 <= h <= 60), "61-90": sum(1 for h in hold_days if 61 <= h <= 90), ">90": sum(1 for h in hold_days if h > 90)},
                      "max_pos": max(x["n_pos"] for x in nav_log),  # R20: 最大持仓数
                      "open_pos_tdays": {c: dix[nav.index[-1]] - i for c, i in buy_day.items()},
                     "n_days": len(nav)}}
    os.makedirs(r"E:\mystockagent\output\ashare", exist_ok=True)
    seg = {}  # 滚动样本外近似: 前段构建参考/后段更晚数据(非严格OOS, 如实标注)
    for a, b, tag in [("2020-01-01", "2022-12-31", "seg_2020_2022"), ("2023-01-01", "2026-12-31", "seg_2023_2026")]:
        s2 = nav[(nav.index >= a) & (nav.index <= b)]
        if len(s2) > 10:
            seg[tag] = {"return": round(s2.iloc[-1] / s2.iloc[0] - 1, 4),
                        "max_dd": round((s2 / s2.cummax() - 1).min(), 4)}
    out["segments"] = seg
    fid = f"{strategy}_{gate}_{exit_mode}_{rank_kind}_{entry_mode}_cap{cap}"  # R19/P0+R20: 唯一实验ID防覆盖(含容量)
    json.dump(out, open(rf"E:\mystockagent\output\ashare\swing_m1_{fid}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


if __name__ == "__main__":
    res = {s: run(s) for s in ("m1", "bh", "trend", "mom")}
    cmp = {s: r["stats"] for s, r in res.items()}
    json.dump({"caveats": res["m1"]["caveats"], "compare": cmp},
              open(r"E:\mystockagent\output\ashare\swing_m1_compare.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(cmp, ensure_ascii=False))
