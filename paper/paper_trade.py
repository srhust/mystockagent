# -*- coding: utf-8 -*-
"""模拟盘候选版 v1.1 (冻结+审查三轮修复)。每日收盘后运行。
修复: ①真实生成时间(datetime.now, 不再写死15:00) ②数据新鲜度检查(指数数据>5个自然日未更新
-> 禁止BUY/SELL, 仅输出观察) ③真实模拟账户(paper/account.json: 现金/持仓/成交),
无持仓不SELL, 已持仓不重复BUY ④成交流水paper/fills.csv含generated_at/data_asof/费用。
冻结规则: 入场 temp_v4<12 ∨ dd<=-max(0.30,0.70×1250日最深); 出场 temp>85 ∨ dd>=-0.25。
冻结依据: Baseline ETF口径 +5.96%/0.50 (etf_ablation_cs.txt v2, 严格OOS对照确认)。"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from carriers import CARRIERS

PAPER = os.path.join(os.path.dirname(config.DATA_DIR), "paper")
os.makedirs(PAPER, exist_ok=True)
ACC = os.path.join(PAPER, "account.json")
FILLS = os.path.join(PAPER, "fills.csv")
STALE_DAYS = 5
START_CASH = 100.0


def temperature(c):
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)


def load_account():
    if os.path.exists(ACC):
        return json.load(open(ACC, encoding="utf-8"))
    return {"cash": START_CASH, "positions": {}, "created": datetime.now().isoformat(timespec="seconds")}


def run(today=None):
    now = datetime.now()
    today = pd.Timestamp(today or now.strftime("%Y-%m-%d"))
    acc = load_account()
    obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
    dfs = obj["dfs"]
    rows, fills = [], []
    for sid, info in CARRIERS.items():
        if not info["enabled"]:
            continue
        cname = info["sector_name"]
        hit = next((n for n in dfs if n == cname or cname in n), None)
        if hit is None:
            continue
        c = dfs[hit]["close"].dropna()
        c.index = pd.to_datetime(c.index)
        c = c[c.index <= today]
        if len(c) < 500:
            continue
        asof = c.index[-1]
        stale = (today - asof).days > STALE_DAYS
        t = temperature(c)
        dd = c / c.cummax() - 1.0
        dep = (-dd).rolling(1250, min_periods=250).max()
        thr = np.maximum(0.30, 0.70 * dep.iloc[-1])
        temp, ddx = float(t.iloc[-1]), float(dd.iloc[-1])
        held = sid in acc["positions"]
        raw = "BUY" if (temp < 12 or ddx <= -thr) else ("SELL" if (temp > 85 or ddx >= -0.25) else "HOLD")
        # 账户状态约束 + 新鲜度门禁
        if stale:
            action, why = "OBSERVE", f"数据陈旧{stale}日(截至{asof.date()}), 禁止交易动作"
        elif raw == "BUY" and held:
            action, why = "HOLD", "已持仓, 不重复买入"
        elif raw == "SELL" and not held:
            action, why = "OBSERVE", "无持仓, 卖出无效"
        else:
            action, why = raw, f"temp={temp:.0f} dd={ddx*100:.0f}% thr=-{thr*100:.0f}%"
        px = float(c.iloc[-1])
        if action == "BUY":
            spend = min(acc["cash"], acc["cash"] * 0.998 / max(1, 6 - len(acc["positions"])))
            fee = spend * 0.001
            sh = (spend - fee) / px
            acc["cash"] -= spend
            acc["positions"][sid] = {"shares": sh, "avg_px": px, "sector": cname, "carrier": info["symbol"]}
            fills.append(dict(generated_at=now.isoformat(timespec="seconds"), data_asof=asof.date().isoformat(),
                              action="BUY", sector=sid, carrier=info["symbol"], price=px,
                              cash_spent=round(spend, 2), fee=round(fee, 3), shares=round(sh, 2)))
        elif action == "SELL":
            pos = acc["positions"].pop(sid)
            proceeds = pos["shares"] * px
            fee = proceeds * 0.001
            acc["cash"] += proceeds - fee
            pnl = (proceeds - fee) / (pos["shares"] * pos["avg_px"] * 1.001) - 1
            fills.append(dict(generated_at=now.isoformat(timespec="seconds"), data_asof=asof.date().isoformat(),
                              action="SELL", sector=sid, carrier=info["symbol"], price=px,
                              cash_in=round(proceeds - fee, 2), fee=round(fee, 3), pnl=round(pnl, 4)))
        rows.append(dict(date=today.date().isoformat(), generated_at=now.isoformat(timespec="seconds"),
                         data_asof=asof.date().isoformat(), stale=int(stale), sector_id=sid, sector=cname,
                         carrier=info["symbol"], raw_signal=raw, action=action, reason=why,
                         temp_v4_price=round(temp, 1), dd=round(ddx, 4), held=int(held),
                         executed="", notes=""))
    if rows:
        L = [f"# 模拟盘信号 {today.date()} (v1.1冻结: 生成于{now.strftime('%H:%M')}, 数据新鲜度门禁{'通过' if not any(r['stale'] for r in rows) else '部分陈旧'})",
             f"> 账户: 现金{acc['cash']:.1f} 持仓{len(acc['positions'])} | 载体9只 | 规则冻结不调参", ""]
        for r in rows:
            L.append(f"- **{r['action']}** {r['sector']}({r['carrier']}): {r['reason']} | 数据截至{r['data_asof']}")
        open(os.path.join(PAPER, f"signals_{today.date().isoformat()}.md"), "w", encoding="utf-8").write("\n".join(L))
        pd.DataFrame(rows).to_csv(os.path.join(PAPER, "log.csv"), mode="a", index=False,
                                  header=not os.path.exists(os.path.join(PAPER, "log.csv")), encoding="utf-8-sig")
    if fills:
        pd.DataFrame(fills).to_csv(FILLS, mode="a", index=False, header=not os.path.exists(FILLS), encoding="utf-8-sig")
    json.dump(acc, open(ACC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"模拟盘v1.1: {len(rows)}信号, {len(fills)}笔成交, 现金{acc['cash']:.1f}, 持仓{len(acc['positions'])}")
    return rows


if __name__ == "__main__":
    run()
