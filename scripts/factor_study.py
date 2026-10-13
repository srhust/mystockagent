# -*- coding: utf-8 -*-
"""
因子增补研究:
A. 区分"过程低温"与"真周期底反转"——temp<12 事件按 ex-ante 因子分组, 看前向收益分离度
B. 下跌加仓摊平(40/30/30 三档) vs 单笔全仓——单板块 100 资金口径
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from backtest_swing import load_inputs  # noqa: E402
from leader_study import load_stock_panel  # noqa: E402
from reversal_study import build_rules  # noqa: E402

inp = load_inputs()
rules = build_rules(inp)
cal, sectors, arr = inp["cal"], inp["sectors"], inp["arr"]
base = rules["entry"]["E0"]
close, temp = arr["close"], arr["temp"]
meta = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))["meta"]

# ---- 因子原料 ----
cdf = pd.DataFrame(close, index=cal, columns=sectors)
ma200 = cdf.rolling(200).mean()
ma200_slope = ma200 / ma200.shift(60) - 1.0          # MA200 60日斜率(长期趋势状态)
dd5y = cdf / cdf.rolling(1250).max() - 1.0           # 距5年高点回撤
mom120 = cdf.pct_change(120)                          # 下跌速度
pairs = sorted({(str(c).zfill(6), s, n) for n, m in meta.items() for c, s in m.get("stocks", [])})
panel, sec_of = load_stock_panel(pairs, cal)
pma200 = panel.rolling(200).mean()
above_frac = (panel > pma200).groupby(sec_of, axis=1).mean()   # 板块内个股MA200上方占比

rows = []
for j, sec in enumerate(sectors):
    prev = np.concatenate([[0.0], base[:-1, j]])
    sig = np.where((base[:, j] > 0) & (prev == 0))[0]
    for d in sig:
        if d + 250 >= len(cal) or d < 1250:
            continue
        f250 = float(cdf.iloc[d + 250][sec] / cdf.iloc[d][sec] - 1.0)
        f120 = float(cdf.iloc[d + 120][sec] / cdf.iloc[d][sec] - 1.0)
        rows.append({
            "sector": sec, "date": cal[d], "f250": f250, "f120": f120,
            "up_trend": float(ma200_slope.iloc[d][sec]) > 0,          # 长期趋势向上?
            "shallow_dd": float(dd5y.iloc[d][sec]) > -0.5,            # 距5年高点不到一半?
            "crash120": float(mom120.iloc[d][sec]) < -0.30,           # 120日跌逾30%(急跌型)?
            "breadth_hi": float(above_frac.iloc[d].get(sec, np.nan)) > 0.3 if sec in above_frac else np.nan,
        })
ev = pd.DataFrame(rows)
obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
fm = {n: (d["fut_mom20"].iloc[-len(cal):] if "fut_mom20" in d.columns else None) for n, d in obj["dfs"].items()}
fut_flag = []
for _, r in ev.iterrows():
    s = fm.get(r["sector"])
    fut_flag.append(np.nan if s is None else float(s.loc[r["date"]] > 0) if r["date"] in s.index else np.nan)
ev["fut_up"] = fut_flag

print(f"[events] temp<12 入场事件(可评估): {len(ev)}")
print("\n== A. 因子分组: 250日前向收益(均值/中位/样本) ==")
for f in ["up_trend", "shallow_dd", "crash120", "breadth_hi", "fut_up"]:
    sub = ev.dropna(subset=[f])
    g = sub.groupby(f)["f250"].agg(["mean", "median", "count"])
    print(f"\n{f}:")
    for k, r in g.iterrows():
        print(f"  {f}={k}: {r['mean'] * 100:+.1f}% / 中位 {r['median'] * 100:+.1f}% (n={int(r['count'])})")
    if len(g) == 2:
        print(f"  → 分离度: {(g['mean'].iloc[1] - g['mean'].iloc[0]) * 100:+.1f}pp")

print("\n== A2. 最优双因子组合 ==")
ev2 = ev.dropna(subset=["up_trend", "breadth_hi"])
if len(ev2) > 100:
    for (a, b), g in ev2.groupby(["up_trend", "breadth_hi"]):
        print(f"  up_trend={a} & breadth_hi={b}: {g['f250'].mean() * 100:+.1f}% / 中位 {g['f250'].median() * 100:+.1f}% (n={len(g)})")

# ---- B. 下跌加仓摊平 vs 单笔全仓 (单板块100资金, 真实持有期=迟滞状态机12/75) ----
print("\n== B. 下跌加仓(40/30/30, -15%/-30%加档) vs 单笔100 ==")
base_h = rules["loop"](rules["entry"]["E0"], rules["exit"]["X0"])
res = {"单笔100": [], "三档摊平": []}
dd_res = {"单笔100": [], "三档摊平": []}
for j, sec in enumerate(sectors):
    prev = np.concatenate([[0.0], base_h[:-1, j]])
    sig = np.where((base_h[:, j] > 0) & (prev == 0))[0]
    col = cdf[sec]
    for d in sig:
        if d + 250 >= len(cal):
            continue
        tail = base_h[d:, j]
        zeros = np.where(tail == 0)[0]
        if len(zeros) == 0 or zeros[0] < 10:
            continue
        end = d + zeros[0] - 1
        hold = col.iloc[d:end + 1]
        if len(hold) < 10:
            continue
        e0 = hold.iloc[0]
        # 单笔
        ret0 = hold.iloc[-1] / e0 - 1.0
        # 三档: -15% 加30, -30% 再加30(各一次)
        units, cost, add_l = [(100, e0)], 100 * e0, [0.85, 0.70]
        for lv in add_l:
            trig = hold[hold <= e0 * lv]
            if len(trig):
                p = trig.iloc[0]
                units.append((30, p))
        tot_u = sum(u for u, _ in units)
        avg_cost = sum(u * p for u, p in units) / tot_u
        ret_a = hold.iloc[-1] / avg_cost - 1.0
        res["单笔100"].append(ret0)
        res["三档摊平"].append(ret_a)
        # 持仓期最大浮亏(相对各自成本)
        dd_res["单笔100"].append(float((hold / e0 - 1.0).min()))
        dd_res["三档摊平"].append(float((hold / avg_cost - 1.0).min()))

for k in res:
    r = np.array(res[k])
    dd = np.array(dd_res[k])
    print(f"  {k}: 单笔均值{r.mean() * 100:+.1f}%/中位{np.median(r) * 100:+.1f}%/"
          f"胜率{(r > 0).mean() * 100:.0f}%/最大浮亏均值{dd.mean() * 100:.1f}%")
ev.to_csv(os.path.join(config.OUTPUT_DIR, "factor_events.csv"), index=False, encoding="utf-8-sig")
print("\n[done] output/factor_events.csv")
