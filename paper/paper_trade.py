# -*- coding: utf-8 -*-
"""模拟盘候选版 v1.2 (冻结+审查五轮修复)。每日收盘后运行。
v1.2修复: ①成交价=ETF真实收盘(data/etf_*.csv), 不再用行业指数价 ②T+1待执行订单:
信号日仅产生pending订单(account.json), 下次运行时以ETF最新收盘执行 ③现金/持仓/费用以ETF口径记账。
保留v1.1: 真实生成时间/数据新鲜度门禁/无持仓禁SELL/已持仓不重复BUY。
冻结规则: 入场 temp_v4<12 ∨ dd<=-max(0.30,0.70×1250日最深); 出场 temp>85 ∨ dd>=-0.25。"""
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
FEE = 0.001


def temperature(c):
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)


def etf_close(sym6, asof=None):
    """ETF真实收盘价: 最新一行(可限<=asof)。"""
    fp = os.path.join(config.DATA_DIR, f"etf_{sym6}.csv")
    s = pd.read_csv(fp, parse_dates=["date"]).rename(columns={"收盘": "close"}).set_index("date")["close"].astype(float)
    if asof is not None:
        s = s[s.index <= pd.Timestamp(asof)]
    return s


def load_account():
    if os.path.exists(ACC):
        return json.load(open(ACC, encoding="utf-8"))
    return {"cash": START_CASH, "positions": {}, "pending": [],
            "created": datetime.now().isoformat(timespec="seconds")}


def _fill(acc, action, sid, info, px, now, asof):
    fee_log = None
    if action == "BUY":
        spend = min(acc["cash"], acc["cash"] * 0.998 / max(1, 6 - len(acc["positions"])))
        fee = spend * FEE
        sh = (spend - fee) / px
        acc["cash"] -= spend
        acc["positions"][sid] = {"shares": sh, "avg_px": px, "sector": info["sector_name"], "carrier": info["symbol"]}
        fee_log = dict(executed_at=now, data_asof=asof, action="BUY", sector=sid, carrier=info["symbol"],
                       etf_price=px, cash_spent=round(spend, 2), fee=round(fee, 3), shares=round(sh, 2))
    else:
        pos = acc["positions"].pop(sid)
        proceeds = pos["shares"] * px
        fee = proceeds * FEE
        acc["cash"] += proceeds - fee
        pnl = (proceeds - fee) / (pos["shares"] * pos["avg_px"] * 1.001) - 1
        fee_log = dict(executed_at=now, data_asof=asof, action="SELL", sector=sid, carrier=info["symbol"],
                       etf_price=px, cash_in=round(proceeds - fee, 2), fee=round(fee, 3),
                       pnl=round(pnl, 4), avg_px=pos["avg_px"])
    return fee_log


def run(today=None):
    now = datetime.now()
    today = pd.Timestamp(today or now.strftime("%Y-%m-%d"))
    acc = load_account()
    obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
    dfs = obj["dfs"]
    fills_log = []

    # --- 1) 执行pending订单: 以各ETF最新真实收盘成交 (T+1机制) ---
    still_pending = []
    for po in acc.get("pending", []):
        sid, info = po["sector"], CARRIERS[po["sector"]]
        s = etf_close(info["symbol"])
        s = s[s.index > pd.Timestamp(po["created_on"])]  # 信号日之后的首个交易日
        if len(s) == 0:
            still_pending.append(po)
            continue
        px = float(s.iloc[-1])
        if po["action"] == "SELL" and sid not in acc["positions"]:
            continue  # 订单失效: 已无持仓
        if po["action"] == "BUY" and (sid in acc["positions"] or acc["cash"] < px * 10):
            continue  # 订单失效: 已持仓或现金不足
        fills_log.append(_fill(acc, po["action"], sid, info, px, now.isoformat(timespec="seconds"),
                               s.index[-1].date().isoformat()))
    acc["pending"] = still_pending

    # --- 2) 生成今日信号 -> 产生新pending(不立即成交) ---
    rows = []
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
        thr = np.maximum(0.30, 0.70 * float((-dd).rolling(1250, min_periods=250).max().iloc[-1]))
        temp, ddx = float(t.iloc[-1]), float(dd.iloc[-1])
        held = sid in acc["positions"]
        has_pending = any(p["sector"] == sid for p in acc["pending"])
        raw = "BUY" if (temp < 12 or ddx <= -thr) else ("SELL" if (temp > 85 or ddx >= -0.25) else "HOLD")
        action = raw
        if stale:
            action, why = "OBSERVE", f"数据陈旧{stale}日(截至{asof.date()}), 禁止交易动作"
        elif raw == "BUY" and (held or has_pending):
            action, why = "HOLD", "已持仓/已挂单, 不重复买入"
        elif raw == "SELL" and not held:
            action, why = "OBSERVE", "无持仓, 卖出无效"
        else:
            why = f"temp={temp:.0f} dd={ddx*100:.0f}% thr=-{thr*100:.0f}%"
        if action in ("BUY", "SELL"):
            acc["pending"] = [p for p in acc["pending"] if p["sector"] != sid]  # 反向订单替换
            acc["pending"].append(dict(action=action, sector=sid, created_on=today.date().isoformat(),
                                       created_at=now.isoformat(timespec="seconds"), reason=why))
            why = f"[挂单待T+1执行] {why}"
        rows.append(dict(date=today.date().isoformat(), generated_at=now.isoformat(timespec="seconds"),
                         data_asof=asof.date().isoformat(), stale=int(stale), sector_id=sid, sector=cname,
                         carrier=info["symbol"], raw_signal=raw, action=action, reason=why,
                         temp_v4_price=round(temp, 1), dd=round(ddx, 4), held=int(held),
                         executed="", notes=""))
    if rows:
        L = [f"# 模拟盘信号 {today.date()} (v1.2: 生成于{now.strftime('%H:%M')}, ETF真实价+T+1挂单)",
             f"> 账户: 现金{acc['cash']:.1f} 持仓{len(acc['positions'])} 挂单{len(acc['pending'])} | 规则冻结", ""]
        for r in rows:
            L.append(f"- **{r['action']}** {r['sector']}({r['carrier']}): {r['reason']} | 数据截至{r['data_asof']}")
        open(os.path.join(PAPER, f"signals_{today.date().isoformat()}.md"), "w", encoding="utf-8").write("\n".join(L))
        pd.DataFrame(rows).to_csv(os.path.join(PAPER, "log.csv"), mode="a", index=False,
                                  header=not os.path.exists(os.path.join(PAPER, "log.csv")), encoding="utf-8-sig")
    if fills_log:
        pd.DataFrame(fills_log).to_csv(FILLS, mode="a", index=False, header=not os.path.exists(FILLS), encoding="utf-8-sig")
    json.dump(acc, open(ACC, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"模拟盘v1.2: {len(rows)}信号, {len(fills_log)}笔执行, 挂单{len(acc['pending'])}, 现金{acc['cash']:.1f}, 持仓{len(acc['positions'])}")
    return rows


if __name__ == "__main__":
    run()
