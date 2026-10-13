# -*- coding: utf-8 -*-
"""唯一可执行回测引擎 (V4.1账本, 指令①): 统一信号输入 + T+1收盘成交 + 现金份额账本 + 风控。
所有研究脚本/每日顾问/模拟盘共用本引擎, 不再各自维护 run()。
约定: ①所有决策输入必须 available_at <= T-1收盘; ②缺价用最近有效价估值(>stale_limit禁止交易);
     ③T日成交按T日收盘价, 当日净值=成交前口径(持仓次日起入净值); ④费用双边各cost。"""
import numpy as np
import pandas as pd


def run_ledger(px, sig, ext, stre, catmap, cal, *, max_pos=6, per_cat=2, cost=0.0015,
               cap=100.0, greedy=False, risk=False, tranches=False, stale_limit=20,
               exposure_cap=0.70, breaker_dd=0.12, sort_key="dd", sig_weight=None):
    """单引擎回测。
    px: DataFrame[cal×names] 收盘价; sig/ext: 0/1 入场/出场信号(T日生成,T+1执行);
    stre: 排序强度(可选); catmap: name->类别; 返回 dict(equity, fills, eqmax_dd)。
    风控: risk=True 时单付认购<=8%净值、总敞口<=exposure_cap(余量法)、净值回撤breaker_dd熔断禁新买。"""
    names = list(px.columns)
    cash = cap
    pos, last_px, stale, entry, stage = {}, {}, {}, {}, {}
    eq = pd.Series(np.nan, index=cal)
    fills = []
    eqmax = cap
    for i, d in enumerate(cal):
        mv = 0.0
        for n, sh in pos.items():
            p = px.at[d, n] if n in px.columns else np.nan
            if np.isfinite(p):
                last_px[n], stale[n] = float(p), 0
            else:
                p = last_px.get(n)
                stale[n] = stale.get(n, 0) + 1
            if p is not None and np.isfinite(p):
                mv += sh * p
        eq.iloc[i] = cash + mv
        eqmax = max(eqmax, eq.iloc[i])
        sd = cal[i - 1] if i > 0 else None
        # 出场: T-1信号, T日执行(需有效价)
        for n in [n for n in list(pos) if sd is not None and ext.at[sd, n] == 1]:
            p = px.at[d, n] if n in px.columns else np.nan
            if np.isfinite(p) and stale.get(n, 0) <= stale_limit:
                cash += pos[n] * p * (1 - cost)
                fills.append((d.date().isoformat(), "SELL", n, pos[n], float(p)))
                del pos[n], entry[n], last_px[n], stale[n], stage[n]
        # 三档摊平加仓(可选)
        if tranches:
            for n in list(pos):
                st = stage.get(n)
                if st is None or st[1] >= 2:
                    continue
                p = px.at[d, n] if n in px.columns else np.nan
                if not np.isfinite(p) or stale.get(n, 0) > stale_limit:
                    continue
                tier = -0.15 if st[1] == 0 else -0.30
                if p <= st[0] * (1 + tier):
                    spend = min(cash * 0.999, eq.iloc[i] / max_pos * 0.35)
                    if spend > p * 0.001:
                        cash -= spend
                        pos[n] += spend * (1 - cost) / p
                        st[1] += 1
                        fills.append((d.date().isoformat(), "ADD", n, spend * (1 - cost) / p, float(p)))
        # 入场
        slots = (10**9 if greedy else max_pos) - len(pos)
        breaker = risk and eq.iloc[i] < eqmax * (1 - breaker_dd)
        buys = []
        if i > 0 and not breaker:
            cand = [n for n in names if n not in pos and sig.at[sd, n] == 1
                    and np.isfinite(px.at[d, n]) and stale.get(n, 0) <= stale_limit]
            cand.sort(key=lambda n: stre.at[sd, n] if (stre is not None and np.isfinite(stre.at[sd, n])) else float("-inf"))
            cats = {}
            for n in pos:
                cats[catmap[n]] = cats.get(catmap[n], 0) + 1
            for n in cand:
                if len(buys) >= slots:
                    break
                if not greedy and cats.get(catmap[n], 0) >= per_cat:
                    continue
                buys.append(n)
                cats[catmap[n]] = cats.get(catmap[n], 0) + 1
        for n in buys:
            p = float(px.at[d, n])
            w = eq.iloc[i] / max_pos if not greedy else eq.iloc[i] / (len(pos) + len(buys))
            if sig_weight is not None:
                if n not in sig_weight.columns or sd not in sig_weight.index:
                    raise ValueError(f"sig_weight缺少权重: {n}@{sd} (审查三轮: 禁止静默回退默认仓位)")
                wm = float(sig_weight.at[sd, n])
                if not np.isfinite(wm) or wm <= 0:
                    raise ValueError(f"sig_weight非法: {n}@{sd}={wm}")
                w *= wm
            spend = min(cash * 0.999, w * (0.30 if tranches else 1.0))
            if risk:
                spend = min(spend, eq.iloc[i] * 0.08)
                spend = min(spend, max(0.0, exposure_cap * eq.iloc[i] - (eq.iloc[i] - cash)))
            if spend > p * 0.001:
                cash -= spend
                sh = spend * (1 - cost) / p
                pos[n] = pos.get(n, 0) + sh
                entry[n] = d
                stage[n] = [p, 0]
                fills.append((d.date().isoformat(), "BUY", n, sh, p))
    return {"equity": eq, "fills": fills, "positions": pos}


def metrics(eq, cost_free_days=244):
    """净值指标: CAGR/Sharpe/Calmar/MDD/回撤恢复天数。"""
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    sharpe = r.mean() / r.std() * np.sqrt(244) if r.std() > 0 else 0.0
    dd = eq / eq.cummax() - 1.0
    mdd = dd.min()
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    trough = dd.idxmin()
    pre_max = eq[:trough].idxmax()
    rec = eq[eq.index > trough]
    hit = rec[rec >= eq.loc[pre_max]]
    rec_days = (hit.index[0] - trough).days if len(hit) else -1
    return {"CAGR": cagr, "Sharpe": sharpe, "Calmar": calmar, "MDD": mdd,
            "trough": trough, "recovery_days": rec_days}


def run_specialist(px, sig, ext, cal, *, budget, cost=0.0015, tranches=(0.30, 0.35, 0.35),
                   tier_dd=(-0.15, -0.30), stale_limit=20, add_sig=None, add_frac=0.35, max_adds=2):
    """V4专户账本 v2 (审查五轮修复):
    ①raw_price与valuation_price分离: 当日无行情(raw非有限)->禁止BUY/ADD/SELL, 仅按last_px估值;
      陈旧>stale_limit->连估值都冻结(不再允许陈旧价成交)。
    ②加仓时间顺序: 用T-1收盘确认 tier 条件(entry_px×(1+dd)), T日执行(与T+1框架一致)。
    tranches=None=一次性全仓; 否则首仓=budget×tranches[0], 加仓=budget×tranches[k+1]。"""
    cash, shares, entry_px, stage = float(budget), 0.0, None, 0
    adds_used = 0
    last_px, stale, eq, fills, trades = None, 0, pd.Series(np.nan, index=cal), [], []
    tiers = [1.0] if tranches is None else list(tranches)
    inv = np.zeros(len(cal))
    for i, d in enumerate(cal):
        raw = float(px.get(d, np.nan))
        if np.isfinite(raw):
            last_px, stale = raw, 0
        else:
            stale += 1
        can_trade = np.isfinite(raw)                       # 审查五轮: 只有当日真实行情才可成交
        vp = raw if can_trade else last_px                 # 估值价
        val = vp if (vp is not None and np.isfinite(vp)) else 0.0
        eq.iloc[i] = cash + shares * val
        inv[i] = shares * val
        sd = cal[i - 1] if i > 0 else None
        prev_px = float(px.get(sd, np.nan)) if sd is not None else np.nan  # T-1收盘(加仓确认用)
        if shares > 0 and can_trade:
            do_exit = bool(ext.get(sd, 0.0)) if sd is not None else False
            do_add = False
            if not do_exit and (stage < len(tiers) - 1 or add_sig is not None):
                if add_sig is not None:
                    do_add = bool(add_sig.get(sd, 0.0)) and adds_used < max_adds  # 顺势加仓: 信号T-1确认
                elif entry_px is not None and np.isfinite(prev_px):
                    do_add = prev_px <= entry_px * (1 + tier_dd[min(stage, len(tier_dd) - 1)])  # 逆势: T-1确认
            if not do_exit and do_add:
                spend = budget * (add_frac if add_sig is not None else tiers[stage + 1])
                if spend <= cash:
                    cash -= spend
                    shares += spend * (1 - cost) / raw
                    stage += 1
                    adds_used += 1
                    fills.append((d.date().isoformat(), "ADD", round(shares, 2), raw))
            if do_exit:
                proceeds = shares * raw * (1 - cost)
                cash += proceeds
                fills.append((d.date().isoformat(), "SELL", round(shares, 2), raw))
                trades.append((d.date().isoformat(), None))
                shares, entry_px, stage, adds_used = 0.0, None, 0, 0
        elif shares == 0 and can_trade and sd is not None and sig.get(sd, 0.0) >= 1 and cash > raw * 0.001:
            spend = min(cash, budget * tiers[0])
            cash -= spend
            shares = spend * (1 - cost) / raw
            entry_px = raw
            fills.append((d.date().isoformat(), "BUY", round(shares, 2), raw))
    eq.attrs["avg_exposure"] = float(np.mean(inv) / np.mean(eq)) if np.mean(eq) > 0 else 0.0
    return eq, fills, trades


def fmt(m):
    return (f"CAGR {m['CAGR']*100:+.2f}% Sharpe {m['Sharpe']:.2f} Calmar {m['Calmar']:.2f} "
            f"MDD {m['MDD']*100:.1f}% 谷底{m['trough'].date()} 恢复{m['recovery_days']}日")



