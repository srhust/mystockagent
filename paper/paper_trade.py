# -*- coding: utf-8 -*-
"""指令⑤: 模拟盘候选版 v1(冻结)。每日运行: 产出信号+可执行动作+可用时间, 记录人工决策与后续表现。
永不自动交易; 阈值冻结: temp_v4<12 ∨ dd<=-max(0.30,0.70×1250日最深); 出场 temp>85 ∨ dd>=-0.25。
载体: 仅carriers.py高保真9只。日志: paper/log.csv (追加), 信号快照: paper/signals_日期.md。"""
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
from engine import metrics  # noqa: F401

PAPER = os.path.join(config.ROOT_DIR if hasattr(config, "ROOT_DIR") else os.path.dirname(config.DATA_DIR), "paper")
os.makedirs(PAPER, exist_ok=True)
THRESH = dict(entry_temp=12.0, entry_dd_floor=0.30, entry_dd_k=0.70, exit_temp=85.0, exit_dd=-0.25)


def temperature(c):
    lo, hi = c.rolling(1250, min_periods=500).min(), c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    return 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)


def run(today=None):
    today = pd.Timestamp(today or datetime.now().strftime("%Y-%m-%d"))
    obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
    dfs = obj["dfs"]
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
        t = temperature(c)
        dd = c / c.cummax() - 1.0
        dep = (-dd).rolling(1250, min_periods=250).max()
        thr = np.maximum(THRESH["entry_dd_floor"], THRESH["entry_dd_k"] * dep)
        temp, ddx, thrx = float(t.iloc[-1]), float(dd.iloc[-1]), float(thr.iloc[-1])
        if temp < THRESH["entry_temp"] or ddx <= -thrx:
            sig, why = "BUY", f"temp={temp:.0f}({'<' if temp < 12 else '≥'}12) dd={ddx*100:.0f}% thr=-{thrx*100:.0f}%"
        elif temp > THRESH["exit_temp"] or ddx >= THRESH["exit_dd"]:
            sig, why = "SELL", f"temp={temp:.0f} dd={ddx*100:.0f}%"
        else:
            sig, why = "HOLD", f"temp={temp:.0f} dd={ddx*100:.0f}% thr=-{thrx*100:.0f}%"
        rows.append(dict(date=today.date().isoformat(), signal_time=f"{today.date().isoformat()} 15:00",
                         sector_id=sid, sector=cname, carrier=info["symbol"], signal=sig,
                         available_at=f"数据截至{c.index[-1].date()}",
                         temp_v4_price=round(temp, 1), dd=round(ddx, 4),
                         reason=why, executed="", notes=""))
    if rows:
        L = [f"# 模拟盘信号 {today.date()} (候选版v1冻结: 入场temp<12∨dd≤-max(0.30,0.70×最深), 出场temp>85∨dd≥-0.25)",
             f"> 信号生成: 收盘后15:00 | 执行: 次日 | 载体: 仅高保真ETF | 产业确认状态见cyclespec", ""]
        for r in rows:
            L.append(f"- **{r['signal']}** {r['sector']}({r['carrier']}): {r['reason']} | {r['available_at']}")
        open(os.path.join(PAPER, f"signals_{today.date().isoformat()}.md"), "w", encoding="utf-8").write("\n".join(L))
        fp = os.path.join(PAPER, "log.csv")
        df = pd.DataFrame(rows)
        df.to_csv(fp, mode="a", index=False, header=not os.path.exists(fp), encoding="utf-8-sig")
        print(f"模拟盘: {len(rows)}载体信号已记录 -> paper/log.csv")
    return rows


if __name__ == "__main__":
    run()
