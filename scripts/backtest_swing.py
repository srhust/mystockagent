# -*- coding: utf-8 -*-
"""
波段买卖点层回测: 基线(一档全进全出) vs 四个独立波段变体 vs 全叠加。

变体规格(父代理架构设计, 全部用现有因子, 阶段门控防接刀):
- S-A 分批兑现:   base exit 上移到 85, 持仓期内 temp 首过 65 → 降至 2/3 仓,
                  首过 75 → 降至 1/3 仓, >85 清仓(阶梯只降不升, 兑现后不回补)
- S-B 乖离减仓:   持仓中 stage∈{expansion,topping} 且 close/ma20-1>0.15 → 减半,
                  乖离回落<0.05 → 恢复, 状态切换间 10 日锁定
- S-C 回调加仓:   持仓中 stage∈{recovery,expansion} 且 35≤temp≤70 且
                  close/ma60-1≤-0.03 且 mom60>0 → +0.5 份(组合上限 110%);
                  乖离回升>0.05 或 temp>75 → 回收加仓部分(一波一次)
- S-D 两段式建仓: temp<12 建 0.6 份; 30 交易日内 temp≥40 或 (temp<8 且 close 创
                  20 日新低) → 补满 1.0; 逾期默认补满

基线模拟器必须先对齐 run_backtest(mode=cold, trailing_stop=False)再谈变体。
"""
import os
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import backtest as btengine  # noqa: E402
from cycle_system import strategy as strat  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.sans-serif"] = config.PLOT_FONTS
plt.rcParams["axes.unicode_minus"] = False
warnings.filterwarnings("ignore", category=FutureWarning)

PKL = os.path.join(config.DATA_DIR, "enriched.pkl")
OUT = config.OUTPUT_DIR
W0 = 1.0 / 6          # 标准仓
MAX_POS = 6
PER_CAT = 2
COST = 0.0015
WINDOWS = [("2010-01-01", "2010-2026 全样本"), ("2017-01-01", "2017-2026 样本外段")]


# ---------------------------------------------------------------- 数据与预计算

def load_inputs():
    obj = pd.read_pickle(PKL)
    dfs, meta = obj["dfs"], obj["meta"]
    sectors = sorted(dfs)
    cal = dfs[sectors[0]].index
    for s in sectors:                      # 全板块日历并集
        cal = cal.union(dfs[s].index)
    n = len(cal)
    inp = {"sectors": sectors, "cal": cal, "cats": [], "arr": {}}

    def col2d(key):
        m = np.full((n, len(sectors)), np.nan)
        for j, s in enumerate(sectors):
            m[:, j] = dfs[s][key].reindex(cal).to_numpy()
        return m

    inp["arr"]["temp"] = col2d("temp")
    inp["arr"]["close"] = col2d("close")
    inp["arr"]["ma20"] = col2d("ma20")
    inp["arr"]["ma60"] = col2d("ma60")
    inp["arr"]["mom60"] = col2d("mom60")
    inp["arr"]["roll20min"] = col2d("close")   # 占位, 下面滚动
    c = pd.DataFrame(inp["arr"]["close"])
    inp["arr"]["roll20min"] = c.rolling(20).min().to_numpy()
    stage = np.empty((n, len(sectors)), dtype=object)
    for j, s in enumerate(sectors):
        stage[:, j] = dfs[s]["stage"].astype(str).reindex(cal).fillna("").to_numpy()
    inp["arr"]["stage"] = stage

    base, base85, strength = (np.zeros((n, len(sectors))) for _ in range(3))
    for j, s in enumerate(sectors):
        base[:, j] = strat.position_target(dfs[s], mode="cold").reindex(cal).fillna(0).to_numpy()
        base85[:, j] = strat.position_target(dfs[s], mode="cold", enter_below=12,
                                             exit_above=85).reindex(cal).fillna(0).to_numpy()
        strength[:, j] = strat.signal_strength(dfs[s]).reindex(cal).fillna(0.0).to_numpy()
    inp["arr"]["base"] = base
    inp["arr"]["base85"] = base85
    inp["arr"]["strength"] = strength

    rets = pd.DataFrame(inp["arr"]["close"]).pct_change().fillna(0.0)
    inp["arr"]["rets"] = rets.to_numpy()

    cats = []
    for s in sectors:
        cats.append(meta.get(s, {}).get("cat") or config.CYCLE_SECTORS.get(s, {}).get("cat", "其他"))
    inp["cats"] = cats
    return inp


# ---------------------------------------------------------------- 模拟器

def simulate(inp, i0, flags, label=""):
    """逐日模拟。flags = dict(sa,sb,sc,sd)。返回 dict(equity, metrics, trades, stats)。"""
    cal, arr, cats, sectors = inp["cal"], inp["arr"], inp["cats"], inp["sectors"]
    k = len(sectors)
    n = len(cal) - i0
    t0 = time.time()

    temp, close = arr["temp"], arr["close"]
    ma20, ma60, mom60, stage = arr["ma20"], arr["ma60"], arr["mom60"], arr["stage"]
    base = arr["base85"] if flags["sa"] else arr["base"]
    strength, rets = arr["strength"], arr["rets"]

    w = np.zeros(k)                     # 昨日权重(参与当日损益)
    equity = 1.0
    eq_peak = 1.0
    pd_state = "normal"                 # P-DIP: 组合回撤买点状态
    eq_curve = np.zeros(n)
    trades = []                         # 主仓完整生命周期记账
    stats = {"sa65": 0, "sa75": 0, "sb_reduce": 0, "sb_restore": 0, "sb_days": 0,
             "sc_add": 0, "sc_win": 0, "sc_pnl": [], "sd_40": 0, "sd_8low": 0, "sd_deadline": 0,
             "sz_add": 0, "sz_win": 0, "sz_pnl": []}

    st = {}                             # 板块状态
    for i in range(n):
        d = i + i0
        # ---- 1. 当日损益: 昨日权重 × 今日收益 ----
        if i > 0:
            equity *= (1.0 + float((w * rets[d]).sum()))
        eq_curve[i] = equity

        # ---- 2. 计算今日目标权重(只处理已持有板块; 新买入统一在买入阶段处理) ----
        tw = np.zeros(k)
        new_state = {}
        for j in list(st.keys()):
            if base[d, j] == 0:          # 卖出(主仓完整生命周期记账)
                s = st.pop(j)
                for tag, px_key in (("sc", "sc_add_px"), ("sz", "sz_px")):
                    if s.get(tag + "_added"):
                        p2 = close[d, j] / s[px_key] - 1.0
                        if np.isfinite(p2):
                            stats[tag + "_pnl"].append(p2)
                            if p2 > 0:
                                stats[tag + "_win"] += 1
                trades.append({"sector": sectors[j], "entry": s["entry_d"], "exit": d,
                               "ret": close[d, j] / s["entry_px"] - 1.0,
                               "days": d - s["entry_d"]})
                continue
            s = st[j]
            # --- S-A 阶梯 ---
            if flags["sa"]:
                lvl_today = 3 if temp[d, j] < 65 else (2 if temp[d, j] < 75 else 1)
                if lvl_today < s["min_level"]:
                    if lvl_today == 2 and s["min_level"] == 3:
                        stats["sa65"] += 1
                    elif lvl_today == 1 and s["min_level"] >= 2:
                        stats["sa75"] += 1
                    s["min_level"] = lvl_today
                sa_f = s["min_level"] / 3.0
            else:
                sa_f = 1.0
            # --- S-D 补满 ---
            if flags["sd"] and not s["filled"]:
                if temp[d, j] >= 40:
                    s["filled"] = True
                    stats["sd_40"] += 1
                elif temp[d, j] < 8 and close[d, j] <= arr["roll20min"][d, j]:
                    s["filled"] = True
                    stats["sd_8low"] += 1
                elif d - s["entry_d"] > 30:
                    s["filled"] = True
                    stats["sd_deadline"] += 1
            sd_f = (0.6 if (flags["sd"] and not s["filled"]) else 1.0)
            # --- S-Z 深回撤承接: 先更新持有期高点, 再判 -20% 深回撤 ---
            s["hold_high"] = max(s.get("hold_high", close[d, j]), close[d, j])
            sz_add_now = False
            if (flags.get("sz") and s["filled"] and not s.get("sz_added")
                    and not s["sc_added"]):
                if (stage[d, j] in ("recovery", "expansion") and temp[d, j] <= 70
                        and close[d, j] <= 0.80 * s["hold_high"]):
                    sz_add_now = True
            # --- S-B 乖离减仓 ---
            sb_f = 1.0
            if flags["sb"] and s["filled"]:
                bias20 = close[d, j] / ma20[d, j] - 1.0 if np.isfinite(ma20[d, j]) else 0.0
                if s["sb_state"] == "normal":
                    if (stage[d, j] in ("expansion", "topping") and bias20 > 0.15
                            and d - s["sb_lock"] >= 10):
                        s["sb_state"], s["sb_lock"] = "reduced", d
                        stats["sb_reduce"] += 1
                else:
                    stats["sb_days"] += 1
                    if bias20 < 0.05 and d - s["sb_lock"] >= 10:
                        s["sb_state"], s["sb_lock"] = "normal", d
                        stats["sb_restore"] += 1
                sb_f = 0.5 if s["sb_state"] == "reduced" else 1.0
            # --- S-C 回调加仓 ---
            sc_add_now = False
            if (flags["sc"] and s["filled"] and not s["sc_added"]
                    and not s.get("sz_added")):
                if (stage[d, j] in ("recovery", "expansion") and 35 <= temp[d, j] <= 70
                        and np.isfinite(ma60[d, j]) and close[d, j] / ma60[d, j] - 1.0 <= -0.03
                        and mom60[d, j] > 0):
                    sc_add_now = True
            base_w = W0 * sd_f * sa_f * sb_f
            new_state[j] = {"s": s, "base_w": base_w, "sc_add_now": sc_add_now,
                            "sz_add_now": sz_add_now}
            tw[j] = base_w + (0.5 * W0 if (s["sc_added"] or s.get("sz_added")) else 0.0)

        # --- S-C/S-Z 组合上限检查(总目标仓 ≤1.05 才允许加仓至 ≤1.0833≤1.1) ---
        if flags["sc"] or flags.get("sz"):
            cur_total = sum(v["base_w"] + (0.5 * W0 if (v["s"]["sc_added"] or
                                                        v["s"].get("sz_added")) else 0.0)
                            for v in new_state.values())
            for j, v in sorted(new_state.items(), key=lambda kv: -strength[d, kv[0]]):
                if v["sc_add_now"] and cur_total <= 1.05:
                    v["s"]["sc_added"] = True
                    v["s"]["sc_add_px"] = close[d, j]
                    v["s"]["sc_add_d"] = d
                    v["sc_add_now"] = False
                    stats["sc_add"] += 1
                    cur_total += 0.5 * W0
                elif v.get("sz_add_now") and cur_total <= 1.05:
                    v["s"]["sz_added"] = True
                    v["s"]["sz_ref"] = v["s"]["hold_high"]
                    v["s"]["sz_px"] = close[d, j]
                    v["sz_add_now"] = False
                    stats["sz_add"] += 1
                    cur_total += 0.5 * W0

        # --- S-C 回收检查(在目标权重里扣除) ---
        for j, v in new_state.items():
            s = v["s"]
            if s["sc_added"]:
                bias60 = close[d, j] / ma60[d, j] - 1.0 if np.isfinite(ma60[d, j]) else 1.0
                if bias60 > 0.05 or temp[d, j] > 75:
                    pnl = close[d, j] / s["sc_add_px"] - 1.0
                    if np.isfinite(pnl):
                        stats["sc_pnl"].append(pnl)
                        if pnl > 0:
                            stats["sc_win"] += 1
                    s["sc_added"] = False
            if flags.get("sz") and s.get("sz_added"):
                if close[d, j] >= 0.95 * s["sz_ref"] or temp[d, j] > 75:
                    pnl = close[d, j] / s["sz_px"] - 1.0
                    if np.isfinite(pnl):
                        stats["sz_pnl"].append(pnl)
                        if pnl > 0:
                            stats["sz_win"] += 1
                    s["sz_added"] = False

        # ---- 3. 卖出优先 ----
        new_w = np.zeros(k)
        held_cats = {}
        for j, v in new_state.items():
            new_w[j] = v["base_w"] + (0.5 * W0 if (v["s"]["sc_added"] or
                                                   v["s"].get("sz_added")) else 0.0)
        # ---- 4. 买入(空位当日回补; 槽位/类别约束) ----
        held = [j for j in new_state if new_w[j] > 0]
        slots = MAX_POS - len(held)
        for j in held:
            c = cats[j]
            held_cats[c] = held_cats.get(c, 0) + 1
        cands = [j for j in range(k) if base[d, j] == 1 and j not in new_state]
        cands.sort(key=lambda j: -strength[d, j])
        for j in cands:
            if slots <= 0:
                break
            c = cats[j]
            if held_cats.get(c, 0) >= PER_CAT:
                continue
            entry_w = W0 * (0.6 if flags["sd"] else 1.0)
            new_w[j] = entry_w
            s = {"entry_d": d, "entry_px": close[d, j], "min_level": 3,
                 "sb_state": "normal", "sb_lock": -999, "sc_added": False,
                 "sc_add_px": np.nan, "sc_add_d": -1,
                 "hold_high": close[d, j], "sz_added": False,
                 "sz_ref": np.nan, "sz_px": np.nan,
                 "filled": not flags["sd"], "fill_deadline": d + 30}
            if flags["sd"]:
                s["filled"] = False
            st[j] = s
            new_state[j] = {"s": s, "base_w": entry_w, "sc_add_now": False}
            held_cats[c] = held_cats.get(c, 0) + 1
            slots -= 1

        # ---- 5. P-DIP 组合回撤买点: 组合回撤≤-20%时用现金缓冲把暴露放大1.25倍
        #      (上限100%不加杠杆), 回撤修复到-8%恢复。与被证伪的"回撤控制"方向相反:
        #      那是-30%卖/-8%买回(高卖低买), 这是-20%买/-8%卖回(低买高卖)。 ----
        if flags.get("pd"):
            dd_p = equity / eq_peak - 1.0
            if pd_state == "normal" and dd_p <= -0.20:
                pd_state = "dip"
            elif pd_state == "dip" and dd_p >= -0.08:
                pd_state = "normal"
            if pd_state == "dip":
                s_sum = float(new_w.sum())
                if s_sum > 1e-9:
                    new_w = new_w * min(1.25, 1.0 / s_sum)

        # ---- 6. 换手成本 ----
        turnover = float(np.abs(new_w - w).sum())
        equity *= (1.0 - turnover * COST)
        eq_curve[i] = equity
        eq_peak = max(eq_peak, equity)
        w = new_w

    eq_df = pd.DataFrame({"equity": eq_curve}, index=cal[i0:])
    tr = pd.DataFrame(trades)
    m = btengine.compute_metrics(eq_df, tr if len(tr) else None, None)
    stats["sc_avg_pnl"] = float(np.mean(stats["sc_pnl"])) if stats["sc_pnl"] else float("nan")
    stats["sz_avg_pnl"] = float(np.mean(stats["sz_pnl"])) if stats["sz_pnl"] else float("nan")
    stats["seconds"] = time.time() - t0
    print(f"[sim] {label}: 年化{m['cagr'] * 100:+.1f}% MDD{m['max_drawdown'] * 100:.1f}% "
          f"Sharpe{m['sharpe']:.2f} 笔数{m.get('n_trades', 0)} | {stats['seconds']:.0f}s")
    return {"equity": eq_df, "metrics": m, "trades": tr, "stats": stats}


# ---------------------------------------------------------------- 主流程

VARIANTS = [
    ("基线(一档进出)", dict(sa=False, sb=False, sc=False, sd=False)),
    ("S-A 分批兑现", dict(sa=True, sb=False, sc=False, sd=False)),
    ("S-B 乖离减仓", dict(sa=False, sb=True, sc=False, sd=False)),
    ("S-C 回调加仓", dict(sa=False, sb=False, sc=True, sd=False)),
    ("S-D 两段建仓", dict(sa=False, sb=False, sc=False, sd=True)),
    ("全叠加", dict(sa=True, sb=True, sc=True, sd=True)),
]


def main():
    t0 = time.time()
    inp = load_inputs()
    print(f"[data] {len(inp['sectors'])} 板块 | {inp['cal'][0].date()} ~ {inp['cal'][-1].date()}")

    # ---- 基线对齐验证 ----
    obj = pd.read_pickle(PKL)
    ref = btengine.run_backtest(
        obj["dfs"], obj["meta"], mode="cold", cost_rate=COST,
        max_positions=MAX_POS, per_category_max=PER_CAT, trailing_stop=False,
        start=inp["cal"][0])
    print(f"[check] run_backtest(trailing_stop=False) 全样本: 年化{ref['metrics']['cagr'] * 100:+.2f}% "
          f"MDD{ref['metrics']['max_drawdown'] * 100:.1f}% 笔数{ref['metrics']['n_trades']}")

    results = {}
    for wstart, wtag in WINDOWS:
        i0 = int(inp["cal"].searchsorted(pd.Timestamp(wstart)))
        print(f"\n===== 窗口 {wtag} ({inp['cal'][i0].date()} 起) =====")
        for name, flags in VARIANTS:
            r = simulate(inp, i0, flags, label=f"{wtag[:9]}|{name}")
            results[(wtag, name)] = r

    # ---- 基线对齐(全样本窗口) ----
    myb = results[("2010-2026 全样本", "基线(一档进出)")]["metrics"]
    gap = abs(myb["cagr"] - ref["metrics"]["cagr"])
    align_ok = gap <= 0.005
    print(f"\n[check] 基线对齐: 模拟器 {myb['cagr'] * 100:+.2f}% vs 引擎 "
          f"{ref['metrics']['cagr'] * 100:+.2f}% (差 {gap * 100:.2f}pp) → "
          f"{'OK' if align_ok else '超出容差, 需排查!'}")

    # ---- 汇总表 ----
    os.makedirs(OUT, exist_ok=True)
    lines = ["# 波段买卖点层回测: 基线 vs 四变体 vs 全叠加\n",
             f"- 生成: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} | 数据修复后 | "
             f"cold 12/75 不含移动止损 | 6 仓等权 | 成本 0.15% | 基线对齐误差 {gap * 100:.2f}pp\n",
             "| 窗口 | 方案 | 年化 | 相对基线 | MDD | Sharpe | 笔数 | 年化波动 |",
             "|---|---|---|---|---|---|---|---|"]
    for wstart, wtag in WINDOWS:
        base_c = results[(wtag, "基线(一档进出)")]["metrics"]["cagr"]
        for name, _f in VARIANTS:
            m = results[(wtag, name)]["metrics"]
            delta = (m["cagr"] - base_c) * 100
            lines.append(
                f"| {wtag} | {name} | {m['cagr'] * 100:+.1f}% | {delta:+.1f}pp | "
                f"{m['max_drawdown'] * 100:.1f}% | {m['sharpe']:.2f} | "
                f"{int(m.get('n_trades', 0) or 0)} | {m.get('ann_vol', float('nan')) * 100:.1f}% |")

    # ---- 波段动作统计(全样本窗口) ----
    lines += ["", "## 波段动作统计（2010-2026 全样本窗口）\n"]
    for name, _f in VARIANTS[1:]:
        s = results[("2010-2026 全样本", name)]["stats"]
        det = []
        if _f.get("sa"):
            det.append(f"阶梯65触发{s['sa65']}次/阶梯75触发{s['sa75']}次")
        if _f.get("sb"):
            det.append(f"减仓{s['sb_reduce']}次/恢复{s['sb_restore']}次/累计减仓{s['sb_days']}天")
        if _f.get("sc"):
            det.append(f"加仓{s['sc_add']}次/胜率"
                       f"{(s['sc_win'] / s['sc_add'] * 100 if s['sc_add'] else 0):.0f}%"
                       f"/单次均值{ s['sc_avg_pnl'] * 100 if np.isfinite(s['sc_avg_pnl']) else 0:+.1f}%")
        if _f.get("sd"):
            det.append(f"补满触发: 温度40上穿{s['sd_40']}次/temp<8新低{s['sd_8low']}次/逾期{s['sd_deadline']}次")
        lines.append(f"- **{name}**: {'; '.join(det) if det else '无动作'}")

    # ---- 2017-18 磨底段专项(S-D 核心问题) ----
    lines += ["", "## 2017-01~2018-12 磨底段专项（S-D 是否少亏）\n",
              "| 方案 | 段内年化 | 段内MDD |", "|---|---|---|"]
    seg0, seg1 = pd.Timestamp("2017-01-01"), pd.Timestamp("2018-12-31")
    for name, _f in VARIANTS:
        eq = results[("2017-2026 样本外段", name)]["equity"]["equity"]
        seg = eq[(eq.index >= seg0) & (eq.index <= seg1)]
        if len(seg) < 50:
            continue
        yrs = (seg.index[-1] - seg.index[0]).days / 365.25
        cagr = (seg.iloc[-1] / seg.iloc[0]) ** (1 / yrs) - 1
        mdd = (seg / seg.cummax() - 1).min()
        lines.append(f"| {name} | {cagr * 100:+.1f}% | {mdd * 100:.1f}% |")

    # ---- 结论占位(由真实数字说话, 生成时填写) ----
    lines += ["", "## 结论\n"]
    for wstart, wtag in WINDOWS:
        base_c = results[(wtag, "基线(一档进出)")]["metrics"]["cagr"]
        best = max(VARIANTS[1:], key=lambda v: results[(wtag, v[0])]["metrics"]["cagr"])
        bm = results[(wtag, best[0])]["metrics"]
        lines.append(f"- {wtag}: 基线 {base_c * 100:+.1f}%; 最优变体 **{best[0]}** "
                     f"{bm['cagr'] * 100:+.1f}% ({(bm['cagr'] - base_c) * 100:+.1f}pp), "
                     f"MDD {bm['max_drawdown'] * 100:.1f}%")

    # ---- 净值曲线(2017 窗口) ----
    fig, ax = plt.subplots(figsize=(12, 6.5))
    for name, _f in VARIANTS:
        eq = results[("2017-2026 样本外段", name)]["equity"]["equity"]
        ax.plot(eq.index, eq.values, lw=1.6, label=name)
    ax.axhline(1.0, color="#bbbbbb", lw=0.8)
    ax.set_title("波段层对比: 基线 vs S-A/S-B/S-C/S-D/全叠加（2017 起, cold 12/75 不含移动止损）")
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "swing_curves.png"), dpi=150)
    plt.close(fig)

    with open(os.path.join(OUT, "swing_compare.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[done] output/swing_compare.md + swing_curves.png | 总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
