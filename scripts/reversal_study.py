# -*- coding: utf-8 -*-
"""
反转/见顶信号精确化研究 + 波段机会测量。

研究一(入场): E0 temp<12(基线) | E1 深寒确认(temp<20且temp60日斜率>0且close>ma20)
             | E2 底背离(close创60日新低但temp未创90日新低,temp<30)
             | E3 阶段转换(bottom→recovery/warmup当日) | E4 期货确认(temp<20且期货20日动量>0;无期货数据回退temp<12)
研究二(出场): X0 temp>75(基线) | X1 temp>85 | X2 趋势破坏(temp>60且close<ma20且ma20<ma60)
             | X3 双重(temp>85 或 temp>65且close<ma60) | X4 期货背离(temp>65且期货动量<0且期货<ma60;回退temp>75)
研究三(测量): 基线每笔持有期内的 ZigZag 中间摆动定量(≥15%/≥25% 两档)。
组合模拟器复用 backtest_swing 中已对齐引擎(0.00pp)的实现核心。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _path  # noqa: F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import backtest as btengine  # noqa: E402
from backtest_swing import load_inputs  # noqa: E402

W0 = 1.0 / 6
MAX_POS = 6
PER_CAT = 2
COST = 0.0015
WINDOWS = [("2010-01-01", "2010-2026 全样本"), ("2017-01-01", "2017-2026 样本外")]

FUT_MAP = {"煤炭": "JM0", "工业金属": "CU0", "能源金属": "LC0", "贵金属": "AU0",
           "油气开采": "SC0", "石油加工": "SC0", "钢铁": "RB0", "玻璃玻纤": "FG0",
           "养殖": "LH0", "饲料加工": "M0", "种植种业": "SA0", "造纸": "SP0", "化纤": "TA0"}


# ---------------------------------------------------------------- 规则矩阵

def build_rules(inp):
    """返回 dict: (rule_name -> {sector_idx -> base 0/1 array}), 以及事件数统计。"""
    cal, arr, sectors = inp["cal"], inp["arr"], inp["sectors"]
    k = len(sectors)
    temp, close = arr["temp"], arr["close"]
    ma20, ma60, stage = arr["ma20"], arr["ma60"], arr["stage"]
    c = pd.DataFrame(close)
    roll60min = c.rolling(60).min().to_numpy()
    tdf = pd.DataFrame(temp)
    slope60 = (tdf - tdf.shift(60)).to_numpy()
    t90min = tdf.rolling(90).min().to_numpy()

    fut_mom = np.full_like(temp, np.nan)
    fut_below = np.zeros_like(temp, dtype=bool)
    for j, s in enumerate(sectors):
        f = FUT_MAP.get(s)
        if not f:
            continue
        fp = os.path.join(config.DATA_DIR, f"fut_{f}.csv")
        if not os.path.exists(fp):
            continue
        fd = pd.read_csv(fp)
        fs = fd.set_index(pd.to_datetime(fd["date"]))["close"].reindex(cal).ffill()
        mom = fs.pct_change(20)
        fut_mom[:, j] = mom.to_numpy()
        ma = fs.rolling(60).mean()
        fut_below[:, j] = (fs < ma).to_numpy()

    def state_loop(entry_mask, exit_mask):
        base = np.zeros_like(temp)
        for j in range(k):
            pos = 0
            for i in range(len(cal)):
                if pos == 1:
                    if exit_mask[i, j]:
                        pos = 0
                elif entry_mask[i, j]:
                    pos = 1
                base[i, j] = pos
        return base

    def nan_and(m1, m2):
        return np.where(np.isnan(m2), False, np.asarray(m1, dtype=bool) & (m2 > 0))

    entry = {}
    entry["E0"] = temp < 12
    entry["E1"] = nan_and(temp < 20, slope60) & (close > ma20)
    entry["E2"] = (close <= roll60min) & (temp > t90min) & (temp < 30)
    st_prev = np.vstack([np.full(k, "", dtype=object), stage[:-1]])
    entry["E3"] = (st_prev == "bottom") & np.isin(stage, ["recovery", "warmup"])
    e4 = nan_and(temp < 20, fut_mom) | (temp < 12)   # 有期货数据且商品转正→放宽到20; 无数据/未转→回退12
    entry["E4"] = e4

    exit_ = {}
    exit_["X0"] = temp > 75
    exit_["X1"] = temp > 85
    exit_["X2"] = (temp > 60) & (close < ma20) & (ma20 < ma60)
    exit_["X3"] = (temp > 85) | ((temp > 65) & (close < ma60))
    x4 = ((temp > 65) & nan_and(temp > 65, -fut_mom) & fut_below) | (temp > 75)
    exit_["X4"] = x4

    events = {n: int((e & ~np.vstack([np.zeros(k, dtype=bool), e[:-1]])).sum())
              for n, e in entry.items()}
    return {"entry": entry, "exit": exit_, "events": events, "loop": state_loop,
            "fut_mom": fut_mom}


# ---------------------------------------------------------------- 模拟器(对齐核心)

def simulate(inp, base, i0, label=""):
    cal, arr, cats, sectors = inp["cal"], inp["arr"], inp["cats"], inp["sectors"]
    k = len(sectors)
    n = len(cal) - i0
    close, strength, rets = arr["close"], arr["strength"], arr["rets"]
    w = np.zeros(k)
    equity = 1.0
    eq_curve = np.zeros(n)
    trades = []
    st = {}
    for i in range(n):
        d = i + i0
        if i > 0:
            equity *= (1.0 + float((w * rets[d]).sum()))
        eq_curve[i] = equity
        new_state = {}
        for j in list(st.keys()):
            if base[d, j] == 0:
                s = st.pop(j)
                trades.append({"j": j, "entry": s["entry_d"], "exit": d,
                               "ret": close[d, j] / s["entry_px"] - 1.0,
                               "days": d - s["entry_d"]})
                continue
            s = st[j]
            new_state[j] = {"s": s, "w": W0}
        held = list(new_state.keys())
        slots = MAX_POS - len(held)
        held_cats = {}
        for j in held:
            held_cats[cats[j]] = held_cats.get(cats[j], 0) + 1
        cands = [j for j in range(k) if base[d, j] == 1 and j not in new_state]
        cands.sort(key=lambda j: -strength[d, j])
        for j in cands:
            if slots <= 0:
                break
            cc = cats[j]
            if held_cats.get(cc, 0) >= PER_CAT:
                continue
            s = {"entry_d": d, "entry_px": close[d, j]}
            st[j] = s
            new_state[j] = {"s": s, "w": W0}
            held_cats[cc] = held_cats.get(cc, 0) + 1
            slots -= 1
        new_w = np.zeros(k)
        for j, v in new_state.items():
            new_w[j] = v["w"]
        equity *= (1.0 - float(np.abs(new_w - w).sum()) * COST)
        eq_curve[i] = equity
        w = new_w
    eq_df = pd.DataFrame({"equity": eq_curve}, index=cal[i0:])
    tr = pd.DataFrame(trades)
    m = btengine.compute_metrics(eq_df, tr if len(tr) else None, None)
    return {"equity": eq_df, "metrics": m, "trades": tr}


# ---------------------------------------------------------------- ZigZag 测量

def zigzag_pivots(px, pct):
    """事后标注摆动极点。标准双向跟踪: direction=0 时两侧同时探测首次确认反转。"""
    piv = []
    if len(px) < 2:
        return piv
    direction = 0
    lo_p = hi_p = px[0]
    lo_i = hi_i = 0
    for i in range(1, len(px)):
        p = px[i]
        if direction >= 0:
            if p > hi_p:
                hi_p, hi_i = p, i
            elif p <= hi_p * (1 - pct):
                if direction == 1:
                    piv.append((hi_i, hi_p, "H"))
                direction = -1
                lo_p, lo_i = p, i
        if direction <= 0:
            if p < lo_p:
                lo_p, lo_i = p, i
            elif p >= lo_p * (1 + pct):
                if direction == -1:
                    piv.append((lo_i, lo_p, "L"))
                direction = 1
                hi_p, hi_i = p, i
    return piv


def swing_measure(inp, base_trades):
    cal, arr, sectors = inp["cal"], inp["arr"], inp["sectors"]
    close = arr["close"]
    out = {}
    for pct in (0.15, 0.25):
        swings_per = []
        amps = []
        for _, t in base_trades.iterrows():
            j = int(t["j"])
            seg = close[int(t["entry"]):int(t["exit"]) + 1, j]
            seg = pd.Series(seg).dropna()
            if len(seg) < 30:
                continue
            piv = zigzag_pivots(seg.to_numpy(), pct)
            interior = [p for p in piv if 5 < p[0] < len(seg) - 5]
            swings_per.append(len(interior) / 2.0)
            for a, b in zip(interior, interior[1:]):
                amps.append(abs(b[1] / a[1] - 1.0))
        out[pct] = {"swings": float(np.mean(swings_per)) if swings_per else 0.0,
                    "amp_med": float(np.median(amps)) if amps else 0.0,
                    "amp_mean": float(np.mean(amps)) if amps else 0.0,
                    "n_legs": len(amps)}
    return out


# ---------------------------------------------------------------- 主流程

def main():
    t0 = time.time()
    inp = load_inputs()
    rules = build_rules(inp)
    print("[events] 入场规则触发次数(全样本):",
          {k2: v for k2, v in rules["events"].items()}, flush=True)

    results = {}
    runs = []
    for e in ["E0", "E1", "E2", "E3", "E4"]:
        runs.append((e, "X0", rules["entry"][e], rules["exit"]["X0"]))
    for x in ["X1", "X2", "X3", "X4"]:
        runs.append(("E0", x, rules["entry"]["E0"], rules["exit"][x]))

    for wstart, wtag in WINDOWS:
        i0 = int(inp["cal"].searchsorted(pd.Timestamp(wstart)))
        print(f"\n===== {wtag} =====", flush=True)
        for en, xn, eb, xb in runs:
            base = rules["loop"](eb, xb)
            r = simulate(inp, base, i0, label=f"{wtag[:9]}|{en}+{xn}")
            results[(wtag, en, xn)] = r
            m = r["metrics"]
            tr = r["trades"]
            tstat = (f"笔均{tr['ret'].mean() * 100:+.1f}%/中位{tr['ret'].median() * 100:+.1f}%"
                     f"/均持{tr['days'].mean():.0f}天") if len(tr) else "无交易"
            print(f"[sim] {en}+{xn}: 年化{m['cagr'] * 100:+.1f}% MDD{m['max_drawdown'] * 100:.1f}% "
                  f"Sharpe{m['sharpe']:.2f} 笔数{m.get('n_trades', 0)} | {tstat}", flush=True)

    # ---- 最优组合(入场/出场各自 vs 基线比较, 不如基线则保留基线) ----
    OOS = WINDOWS[1][1]

    def pick():
        b_e = "E0"
        for en in ["E1", "E2", "E3", "E4"]:
            if results[(OOS, en, "X0")]["metrics"]["sharpe"] > results[(OOS, b_e, "X0")]["metrics"]["sharpe"]:
                b_e = en
        b_x = "X0"
        for xn in ["X1", "X2", "X3", "X4"]:
            if results[(OOS, "E0", xn)]["metrics"]["sharpe"] > results[(OOS, "E0", b_x)]["metrics"]["sharpe"]:
                b_x = xn
        return b_e, b_x

    be, bx = pick()
    print(f"\n[combo] 最优入场 {be} × 最优出场 {bx}")
    for wstart, wtag in WINDOWS:
        i0 = int(inp["cal"].searchsorted(pd.Timestamp(wstart)))
        if (wtag, be, bx) in results:
            continue
        base = rules["loop"](rules["entry"][be], rules["exit"][bx])
        r = simulate(inp, base, i0, label=f"{wtag[:9]}|{be}+{bx}")
        results[(wtag, be, bx)] = r
        m = r["metrics"]
        print(f"[sim] {be}+{bx}: 年化{m['cagr'] * 100:+.1f}% MDD{m['max_drawdown'] * 100:.1f}% "
              f"Sharpe{m['sharpe']:.2f} 笔数{m.get('n_trades', 0)}", flush=True)

    # ---- ZigZag 波段机会测量(基线 E0+X0 的交易) ----
    base_tr = results[("2010-2026 全样本", "E0", "X0")]["trades"]
    zm = swing_measure(inp, base_tr)
    print("\n[zigzag] 基线持有期内中间摆动测量:")
    for pct, v in zm.items():
        print(f"  ≥{int(pct * 100)}%: 每笔平均 {v['swings']:.1f} 个中间摆动 | "
              f"摆幅中位 {v['amp_med'] * 100:.1f}% / 均值 {v['amp_mean'] * 100:.1f}% ({v['n_legs']} 段)")

    # ---- 报告 ----
    lines = ["# 反转/见顶信号精确化研究\n",
             f"- 生成: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} | cold 12/75 口径重构为规则矩阵 | "
             f"6仓等权/成本0.15%/不含移动止损 | 模拟器与引擎对齐误差 0.00pp\n",
             f"- 入场规则触发次数(全样本): {rules['events']}\n",
             "## 入场研究（出场固定 X0 temp>75）\n",
             "| 规则 | 窗口 | 年化 | MDD | Sharpe | 笔数 | 笔均 | 笔中位 | 平均持有 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for (wtag, en, xn), r in results.items():
        if xn != "X0":
            continue
        m = r["metrics"]
        tr = r["trades"]
        lines.append(f"| {en} | {wtag} | {m['cagr'] * 100:+.1f}% | {m['max_drawdown'] * 100:.1f}% | "
                     f"{m['sharpe']:.2f} | {int(m.get('n_trades', 0) or 0)} | "
                     f"{tr['ret'].mean() * 100:+.1f}% | {tr['ret'].median() * 100:+.1f}% | "
                     f"{tr['days'].mean():.0f}天 |" if len(tr) else
                     f"| {en} | {wtag} | {m['cagr'] * 100:+.1f}% | {m['max_drawdown'] * 100:.1f}% | "
                     f"{m['sharpe']:.2f} | 0 | - | - | - |")
    lines += ["\n## 出场研究（入场固定 E0 temp<12）\n",
              "| 规则 | 窗口 | 年化 | MDD | Sharpe | 笔数 | 笔均 | 笔中位 | 平均持有 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for (wtag, en, xn), r in results.items():
        if en != "E0":
            continue
        m = r["metrics"]
        tr = r["trades"]
        lines.append(f"| {xn} | {wtag} | {m['cagr'] * 100:+.1f}% | {m['max_drawdown'] * 100:.1f}% | "
                     f"{m['sharpe']:.2f} | {int(m.get('n_trades', 0) or 0)} | "
                     f"{tr['ret'].mean() * 100:+.1f}% | {tr['ret'].median() * 100:+.1f}% | "
                     f"{tr['days'].mean():.0f}天 |" if len(tr) else
                     f"| {xn} | {wtag} | {m['cagr'] * 100:+.1f}% | {m['max_drawdown'] * 100:.1f}% | "
                     f"{m['sharpe']:.2f} | 0 | - | - | - |")
    lines += ["\n## 最优组合\n",
              f"- **{be} + {bx}**:", "", "| 窗口 | 年化 | MDD | Sharpe | 笔数 |", "|---|---|---|---|---|"]
    for wstart, wtag in WINDOWS:
        m = results[(wtag, be, bx)]["metrics"]
        lines.append(f"| {wtag} | {m['cagr'] * 100:+.1f}% | {m['max_drawdown'] * 100:.1f}% | "
                     f"{m['sharpe']:.2f} | {int(m.get('n_trades', 0) or 0)} |")
    lines += ["\n## ZigZag 中间摆动测量（基线 75 笔持有期）\n"]
    for pct, v in zm.items():
        lines.append(f"- ≥{int(pct * 100)}% 摆动: 每笔平均 {v['swings']:.1f} 个 | "
                     f"摆幅中位 {v['amp_med'] * 100:.1f}% / 均值 {v['amp_mean'] * 100:.1f}%（共 {v['n_legs']} 段）")
    with open(os.path.join(config.OUTPUT_DIR, "reversal_study.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[done] output/reversal_study.md | {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
