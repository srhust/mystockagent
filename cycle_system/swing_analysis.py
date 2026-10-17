# -*- coding: utf-8 -*-
"""R24: 归因器v2——①逐日P&L对账闭合(前收盘持仓→当日开盘成交→当日收盘估值, 严格对齐ΔNAV)
②FIFO lot匹配(多次买/部分卖/open单列) ③退出后5/20/60日(避险/卖飞/中性) ④行业领先性。
MAE/MFE口径: 收盘价路径(panel无high/low, 如实标注)。"""
import json
import sys
from collections import deque

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\mystockagent\cycle_system")
import swing_m1 as m

panel = m.load_panel()
close = panel.pivot(index="date", columns="code", values="close").ffill()  # R24: 最近有效价语义对齐run(last_px); 停牌日Δ=0, MAE/MFE口径=ffill收盘路径(如实标注)
sector_of = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()
didx = {d: i for i, d in enumerate(close.index)}


def analyze(tag: str) -> dict:
    d = json.load(open(rf"E:\mystockagent\output\ashare\swing_m1_{tag}_full.json", encoding="utf-8"))
    nav = pd.Series({pd.Timestamp(x["date"]): x["nav"] for x in d["nav_log"]}).sort_index()
    fills = sorted([t for t in d["trade_log"] if t["reason"] == "FILLED"], key=lambda x: x["oid"])
    by_day = {}
    for t in fills:
        by_day.setdefault(t["exec_day"], []).append(t)

    # ── P0-1: 逐日对账闭合(事件序: 前收盘持仓→开盘成交→收盘估值) ──
    pos = {c: 0 for c in close.columns}
    recon, rows = [], []
    dates = list(nav.index)
    cash_recon = 20000.0  # R24: 现金重放(对账锚)
    for k in range(1, len(dates)):
        dt, prev = dates[k], dates[k - 1]
        trades = by_day.get(str(dt.date()), [])
        pos_prev_val = sum(pos[c] * close.at[prev, c] for c in pos if pos[c] and prev in close.index)
        cash_prev = cash_recon
        overnight = sum(pos[c] * (close.at[dt, c] - close.at[prev, c]) for c in pos if pos[c] and dt in close.index)
        cash_flow, fees_day = 0.0, 0.0
        for t in trades:
            c, sh, px = t["code"], t["shares"], t["px"]
            if px == 0:
                continue
            pos[c] += (sh if t["action"] == "BUY" else -sh)
            cash_flow += (-px * sh if t["action"] == "BUY" else px * sh)  # R24修正: 卖出现金入账, 市值减少由pos_val_after体现(不重复计px_exec)
            fees_day += t["fees"]
        cash_recon += cash_flow - fees_day
        pos_after_val = sum(pos[c] * close.at[dt, c] for c in pos if pos[c] and dt in close.index)
        total = (cash_recon - cash_prev) + (pos_after_val - pos_prev_val)  # = Δcash + Δposval(唯一恒等式)
        expect = nav.at[dt] - nav.at[prev]
        gap = expect - total
        recon.append(gap)
        rows.append({"date": str(dt.date()), "overnight": round(overnight, 2), "intraday": round(total - overnight - (cash_flow - fees_day), 2),
                     "cashflow": round(cash_flow - fees_day, 2), "expect": round(float(expect), 2), "gap": round(float(gap), 2)})
    gi = max(range(len(recon)), key=lambda i: abs(recon[i]))
    if abs(recon[gi]) > 1.0:
        wd = rows[gi]["date"]
        print('   [dbg] worst-gap row:', rows[gi])
    max_gap = max(abs(g) for g in recon)

    # ── P0-2: FIFO lot + MAE/MFE(收盘价路径) ──
    lots = {}
    closed, open_lots = [], []

    def lot_mae(lot, end_day, c):
        seg = close.loc[lot["d0"]:pd.Timestamp(end_day), c].dropna()
        if len(seg) < 2:
            return None
        r = seg / lot["px"] - 1
        return {"code": c, "ret": round(float(seg.iloc[-1] / lot["px"] - 1), 4), "mae": round(float(r.min()), 4),
                "mfe": round(float(r.max()), 4), "tdays": len(seg)}

    for t in fills:
        c, sh, px = t["code"], t["shares"], float(t["px"])
        if px == 0:
            continue
        q = lots.setdefault(c, deque())
        if t["action"] == "BUY":
            q.append({"d0": pd.Timestamp(t["exec_day"]), "px": px, "sh": sh})
        else:
            need = sh
            while need > 0 and q:
                lot = q[0]
                take = min(need, lot["sh"])
                rec = lot_mae(lot, t["exec_day"], c)
                if rec:
                    closed.append(rec)
                need -= take
                lot["sh"] -= take
                if lot["sh"] <= 0:
                    q.popleft()
    for c, q in lots.items():
        for lot in q:
            rec = lot_mae(lot, str(close.index[-1].date()), c)
            if rec:
                open_lots.append(rec)

    # ── P1: 退出后20日(避险/卖飞/中性) ──
    post = {"avoid": 0, "whipsaw": 0, "neutral": 0, "detail": []}
    for t in fills:
        if t["action"] != "SELL" or t["px"] == 0:
            continue
        i0 = didx.get(pd.Timestamp(t["exec_day"]))
        if i0 is None or i0 + 20 >= len(close.index):
            continue
        try:
            r20 = float(close.iloc[i0 + 20][t["code"]] / t["px"] - 1)
        except KeyError:
            continue
        post["avoid" if r20 < 0 else ("whipsaw" if r20 > 0.05 else "neutral")] += 1
        post["detail"].append({"code": t["code"], "day": t["exec_day"], "r20": round(r20, 4)})

    # ── P1: 行业状态领先性(maxDD窗口股票/行业贡献) ──
    dd = nav / nav.cummax() - 1
    ti = dd.idxmin()
    pi = nav.loc[:ti].idxmax()
    pos2 = {c: 0 for c in close.columns}
    stock_dd = {}
    op = panel.pivot(index="date", columns="code", values="open_raw").ffill()  # R25: 隔夜/盘中拆分(缺开盘用前收, 与主回测一致)
    for k in range(1, len(dates)):
        dt, prev = dates[k], dates[k - 1]
        if dt > ti:
            break
        tds = [t for t in by_day.get(str(dt.date()), []) if t["px"] != 0]
        pos_open = dict(pos2)  # 隔夜段持仓(前日收盘)
        for t in tds:
            pos2[t["code"]] += t["shares"] if t["action"] == "BUY" else -t["shares"]
        if dt >= pi and dt in close.index and prev in close.index:
            for c in set(pos_open) | set(pos2):
                q0, q1 = pos_open.get(c, 0), pos2.get(c, 0)
                if not q0 and not q1:
                    continue
                o = op.at[dt, c] if c in op.columns else np.nan
                c0, c1 = close.at[prev, c], close.at[dt, c]
                if not (o == o and c0 == c0 and c1 == c1):
                    continue
                v = q0 * (o - c0) + q1 * (c1 - o)  # R25审查者公式: 隔夜+盘中
                if v:
                    stock_dd[c] = stock_dd.get(c, 0.0) + v
    sector_dd = {}
    for c, v in stock_dd.items():
        sector_dd[sector_of.get(c, "?")] = sector_dd.get(sector_of.get(c, "?"), 0.0) + v

    return {"maxdd": round(float(dd.min()), 4), "peak": str(pi.date()), "trough": str(ti.date()),
            "recon_max_gap": round(float(max_gap), 2),
            "dd_sector_top8": {k: round(v) for k, v in sorted(sector_dd.items(), key=lambda x: x[1])[:8]},
            "mae_mfe": {"closed_n": len(closed), "open_n": len(open_lots),
                        "avg_mae": round(float(np.mean([x["mae"] for x in closed])), 4) if closed else None,
                        "avg_mfe": round(float(np.mean([x["mfe"] for x in closed])), 4) if closed else None,
                        "avg_tdays": round(float(np.mean([x["tdays"] for x in closed])), 1) if closed else None,
                        "mae_bucket": {"<-40%": sum(1 for x in closed if x["mae"] <= -0.40), "-40~-25%": sum(1 for x in closed if -0.40 < x["mae"] <= -0.25), "-25~-10%": sum(1 for x in closed if -0.25 < x["mae"] <= -0.10), ">-10%": sum(1 for x in closed if x["mae"] > -0.10)},
                        "open_avg_mae": round(float(np.mean([x["mae"] for x in open_lots])), 4) if open_lots else None},
            "post_exit_price_direction": {"down": post["avoid"], "flat": post["neutral"], "up>5pct": post["whipsaw"], "denom_all": post["avoid"] + post["neutral"] + post["whipsaw"], "note": "价格方向统计, 非交易正确率(未计替代收益/费用/风险)"},
            "whipsaw_top10": sorted(post["detail"], key=lambda x: -x["r20"])[:10]}


out = {t: analyze(t) for t in ("B0", "B1", "B3")}
json.dump(out, open(r"E:\mystockagent\output\ashare\swing_dd_attribution_v2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
for t, o in out.items():
    print(t, "| recon_gap:", o["recon_max_gap"], "| maxDD:", o["maxdd"], o["peak"], "→", o["trough"])
    print("   sectors:", json.dumps(o["dd_sector_top8"], ensure_ascii=False))
    print("   MAE/MFE:", json.dumps(o["mae_mfe"], ensure_ascii=False)[:280])
    print("   exit20d:", json.dumps(o["post_exit_price_direction"], ensure_ascii=False))
