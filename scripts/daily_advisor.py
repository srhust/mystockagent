# -*- coding: utf-8 -*-
"""
每日决策工具: 捕捉市场状态 → 组合底盘操作建议 → 专户进攻仓位 → 风险预警。
用法: python main.py --no-fetch 刷新数据后, python scripts/daily_advisor.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import strategy as strat  # noqa: E402

obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta, ts = obj["dfs"], obj["meta"], obj.get("ts", "?")
CATS = {n: m.get("cat", "") for n, m in meta.items()}

L = [f"# 每日决策报告 {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}（数据截至 {ts}）\n"]

# ---------- 1. 市场概览 ----------
rows = []
for n, df in dfs.items():
    last = df.iloc[-1]
    fm = last.get("fut_mom20", np.nan)
    fb = last.get("fut_below_ma60", False)
    pos = int(strat.position_target(df, mode="cold").iloc[-1])
    rows.append({"sector": n, "cat": CATS[n], "temp": last["temp"], "stage": last["stage"],
                 "pos": pos, "fut_mom": fm, "fut_below": bool(fb) if np.isfinite(fm) else False,
                 "strength": float(strat.signal_strength(df).iloc[-1])})
T = pd.DataFrame(rows).sort_values("temp")
zones = {"深寒<12": (T.temp < 12).sum(), "冷12-25": ((T.temp >= 12) & (T.temp < 25)).sum(),
         "中25-60": ((T.temp >= 25) & (T.temp < 60)).sum(), "热60-75": ((T.temp >= 60) & (T.temp < 75)).sum(),
         "过热>75": (T.temp >= 75).sum()}
L.append("## 市场概览\n- 温度分布: " + " | ".join(f"{k}: {v}" for k, v in zones.items())
         + f"\n- 阶段: bottom {sum(T.stage == 'bottom')} | recovery {sum(T.stage == 'recovery')}"
           f" | expansion {sum(T.stage == 'expansion')} | topping {sum(T.stage == 'topping')}"
           f" | decline {sum(T.stage == 'decline')}\n")

# ---------- 2. 组合底盘(6仓轮动) ----------
L.append("## 组合底盘（6仓等权, 每仓1/6≈16.7%, 同类≤2）")
holds = T[T.pos == 1].sort_values("temp", ascending=False)
cold = T[(T.pos == 0) & (T.temp < 12)].sort_values("strength", ascending=False)
L.append(f"- 持仓 {len(holds)}/6: " + ("、".join(f"{r.sector}({r.temp:.0f}°)" for r in holds.itertuples()) or "无"))
for r in holds.itertuples():
    if r.temp > 75:
        L.append(f"- **卖出 {r.sector}**: temp {r.temp:.0f}>75, 腾出槽位")
    elif r.temp > 65:
        dv = "⚠期货背离" if (r.fut_mom < 0 and r.fut_below) else ""
        L.append(f"- 观察 {r.sector}: temp {r.temp:.0f}>65 卖出预警 {dv}")
if len(holds) < 6 and len(cold):
    slots = 6 - len(holds)
    pick = []
    cat_n = holds.groupby("cat").size().to_dict()
    for r in cold.itertuples():
        if slots <= 0:
            break
        if cat_n.get(r.cat, 0) < 2:
            pick.append(r)
            cat_n[r.cat] = cat_n.get(r.cat, 0) + 1
            slots -= 1
    for r in pick:
        L.append(f"- **买入 {r.sector}**: temp {r.temp:.0f}<12, 买入 1/6 仓位(≈16.7%), 类别 {r.cat}")
if not len(cold) and len(holds) < 6:
    L.append("- 无新买入信号: 空槽位等待 temp<12 (纪律: 不抢反弹)")
L.append("")

# ---------- 3. 专户进攻(单行业100资金, 30/35/35, 非金融85/金融75出场) ----------
L.append("## 专户进攻（单行业100资金: 30入场 → -15%加35 → -30%加35 → 出场）")
for r in T.itertuples():
    df = dfs[r.sector]
    ex = 75 if r.cat.startswith("金融") else 85
    if r.pos == 1:
        sig_days = df.index[df["temp"] < 12]
        if len(sig_days) == 0:
            continue
        e_day = sig_days[-1]
        e_px = float(df["close"].loc[e_day])
        hold_px = df["close"].loc[e_day:]
        adds = []
        used = 0
        for lv, w in [(-0.15, 35), (-0.30, 35)]:
            if used < 2 and (hold_px <= e_px * (1 + lv)).any():
                px = float(hold_px[hold_px <= e_px * (1 + lv)].iloc[0])
                adds.append(f"{lv * 100:.0f}%档已加35@{px:.2f}")
                used += 1
        tot = 30 + 35 * used
        avg_c = (30 * e_px + sum(35 * float(df['close'].loc[e_day:]) [ (df['close'].loc[e_day:] <= e_px*(1+lv)).idxmax() ] for lv, w in [(-0.15,35),(-0.30,35)][:used])) / tot if False else None
        units = [(30, e_px)] + [(35, float(df["close"].loc[e_day:])[(df["close"].loc[e_day:] <= e_px * (1 + lv))].iloc[0]) for lv, _ in [(-0.15, 0), (-0.30, 0)][:used]]
        avg_c = sum(u * p for u, p in units) / (30 + 35 * used)
        fl = float(df["close"].iloc[-1]) / avg_c - 1
        nxt = f"下一档: {'-' if used>=2 else ['15%','30%'][used]}加35" if used < 2 else "已满仓100"
        L.append(f"- **{r.sector}** 持仓: 已投入{tot}/100, 均价{avg_c:.2f}, 浮动{fl * 100:+.1f}%, "
                 f"出场 temp>{ex}(当前{r.temp:.0f}°), {nxt}")
    elif r.temp < 25:
        px = float(df["close"].iloc[-1])
        L.append(f"- 候选 {r.sector}({r.cat}, {r.temp:.0f}°{r.stage}): 触发temp<12后买30, "
                 f"加档参考价 {px * 0.85:.2f}(-15%) / {px * 0.70:.2f}(-30%), 出场temp>{ex}")
L.append("")
L.append("> 纪律: ①温度<12才买, 不等确认; ②加仓按价格档位机械执行; ③金融类出场75, 其余85; "
         "④专户资金独立于组合, 单行业集中风险自担。")

with open(os.path.join(config.OUTPUT_DIR, "daily_advice.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("\n".join(L))
