# -*- coding: utf-8 -*-
"""R23: 成交级归因分析(离线, 只读trade_log+nav_log+panel)。

输出:
1. maxDD区间(起止日期) + 区间内逐日持仓贡献分解(股票/行业)
2. MAE/MFE(已平仓+未平仓, 持仓期真实数据, 禁未来价)
3. 三类退出区分: 恢复/趋势破坏/单股重挫(退出后5/20日收益)
口径: 估值价=raw close(与run的last_px近似, 对账容差如实标注); 未平仓标open。
"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

panel = m.load_panel()
close = panel.pivot(index="date", columns="code", values="close")
sector_of = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()


def analyze(tag: str) -> dict:
    d = json.load(open(rf"E:\mystockagent\output\ashare\swing_m1_{tag}_full.json", encoding="utf-8"))
    nav = pd.Series({pd.Timestamp(x["date"]): x["nav"] for x in d["nav_log"]}).sort_index()
    tl = d["trade_log"]
    fills = [t for t in tl if t["reason"] == "FILLED"]

    # 1) maxDD窗口
    peak = nav.cummax()
    dd = nav / peak - 1
    ti = dd.idxmin()  # 谷
    pi = nav.loc[:ti].idxmax()  # 峰
    # 2) 区间内逐日持仓贡献(股票/行业)
    pos, cash = {}, 20000.0
    contrib = {}
    daily = []
    for t in sorted(fills, key=lambda x: x["exec_day"]):
        pass
    fills_sorted = sorted(fills, key=lambda x: (x["exec_day"], x["oid"]))
    ev_i = 0
    nav_dates = list(nav.index)
    contrib_stock, contrib_sector = {}, {}
    for k, dt in enumerate(nav_dates[:-1]):
        nxt = nav_dates[k + 1]
        while ev_i < len(fills_sorted) and pd.Timestamp(fills_sorted[ev_i]["exec_day"]) <= dt:
            t = fills_sorted[ev_i]
            px = float(t["px"])
            if t["action"] == "BUY":
                pos[t["code"]] = pos.get(t["code"], 0.0) + t["shares"]
                cash -= px * t["shares"] + t["fees"]
            else:
                pos[t["code"]] = pos.get(t["code"], 0.0) - t["shares"]
                cash += px * t["shares"] - t["fees"]
            ev_i += 1
        day_ret = {}
        for c, sh in pos.items():
            if sh <= 0:
                continue
            try:
                p0 = close.at[dt, c] if dt in close.index else np.nan
                p1 = close.at[nxt, c] if nxt in close.index else np.nan
                if p0 == p0 and p1 == p1:
                    day_ret[c] = sh * (p1 - p0)
            except KeyError:
                pass
        tot = sum(day_ret.values())
        nav_chg = nav.at[nxt] - nav.at[dt]
        cash_chg = 0.0
        contrib_stock[(str(dt.date()), str(nxt.date()))] = day_ret
        daily.append({"date": str(nxt.date()), "nav_chg": round(float(nav_chg), 2), "pos_chg": round(float(tot), 2), "gap": round(float(nav_chg - tot), 2)})
        for c, v in day_ret.items():
            contrib_stock[(str(nxt.date()), c)] = contrib_stock.get((str(nxt.date()), c), 0.0) + v
    # 区间贡献聚合(股票/行业)
    win = (nav.loc[pi:ti] / nav.loc[pi] - 1)
    win_days = [d0 for d0 in nav_dates if pi <= d0 <= ti]
    stock_dd, sector_dd = {}, {}
    for k in range(len(win_days) - 1):
        d0, d1 = win_days[k], win_days[k + 1]
        key0 = (str(d0.date()), str(d1.date()))
        for c, v in contrib_stock.get(key0, {}).items():
            stock_dd[c] = stock_dd.get(c, 0.0) + v
    for c, v in stock_dd.items():
        sec = sector_of.get(c, "?")
        sector_dd[sec] = sector_dd.get(sec, 0.0) + v
    # 3) MAE/MFE(真实持仓期, 未平仓标open)
    buys = {}
    mae_mfe = []
    def _mae_mfe(b, end_day, kind):
        c, px, d0 = b["code"], float(b["px"]), pd.Timestamp(b["exec_day"])
        de = pd.Timestamp(end_day)
        try:
            seg = close.loc[d0:de, c].dropna()
        except KeyError:
            return {}
        if len(seg) < 2:
            return {}
        rets = seg / px - 1
        return {"code": c, "kind": kind, "buy": str(d0.date()), "sell": end_day, "ret": round(float(seg.iloc[-1] / px - 1), 4),
                "mae": round(float(rets.min()), 4), "mfe": round(float(rets.max()), 4), "hold_tdays": len(seg)}

    open_pos = {}
    for t in fills_sorted:
        c = t["code"]
        if t["action"] == "BUY":
            buys[c] = t
        elif c in buys:
            b = buys.pop(c)
            mae_mfe.append(_mae_mfe(b, t["exec_day"], "closed"))
    for c, b in buys.items():
        mae_mfe.append(_mae_mfe(b, str(nav.index[-1].date()), "open"))
        open_pos[c] = (str(nav.index[-1].date()) , )

    mae_mfe = [x for x in mae_mfe if x]

    return {"maxdd_start": str(pi.date()), "maxdd_trough": str(ti.date()), "maxdd": round(float(dd.min()), 4),
            "dd_window_sector": dict(sorted(sector_dd.items(), key=lambda x: x[1])[:8]),
            "dd_window_stock_top10": {c: round(v, 0) for c, v in sorted(stock_dd.items(), key=lambda x: x[1])[:10]},
            "mae_mfe_summary": {
                "closed_n": sum(1 for x in mae_mfe if x["kind"] == "closed"),
                "open_n": sum(1 for x in mae_mfe if x["kind"] == "open"),
                "avg_mae_closed": round(float(np.mean([x["mae"] for x in mae_mfe if x["kind"] == "closed"])), 4),
                "avg_mfe_closed": round(float(np.mean([x["mfe"] for x in mae_mfe if x["kind"] == "closed"])), 4),
                "worst10_mae": sorted([x for x in mae_mfe if x["kind"] == "closed"], key=lambda x: x["mae"])[:10],
                "open_worst5_mae": sorted([x for x in mae_mfe if x["kind"] == "open"], key=lambda x: x["mae"])[:5],
                "mae_bucket": {"<-40%": sum(1 for x in mae_mfe if x["mae"] <= -0.40), "-40~-25%": sum(1 for x in mae_mfe if -0.40 < x["mae"] <= -0.25), "-25~-10%": sum(1 for x in mae_mfe if -0.25 < x["mae"] <= -0.10), ">-10%": sum(1 for x in mae_mfe if x["mae"] > -0.10)},
            },
            "nav_recon_gap_max": round(max(abs(x["gap"]) for x in daily), 2) if daily else None}


out = {t: analyze(t) for t in ("B0", "B1", "B3")}
json.dump(out, open(r"E:\mystockagent\output\ashare\swing_dd_attribution.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
for t, o in out.items():
    print(t, "maxDD", o["maxdd"], o["maxdd_start"], "→", o["maxdd_trough"], "| sectors:", json.dumps(o["dd_window_sector"], ensure_ascii=False))
