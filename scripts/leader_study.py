# -*- coding: utf-8 -*-
"""
龙头股前向检验: 板块深寒反转信号日(E0 temp<12), 用当时已知信息在板块内选龙头,
衡量信号日后 60/120/250 日 龙头篮子 vs 板块指数 的超额。

龙头定义(全部 ex-ante, 无前视):
- 候选: 板块内上市满 250 交易日、信号日有报价的个股
- 主排序: 60 日超额动量 = 个股60日收益 − 板块指数60日收益
- 次排序: 120 日 α = 个股120日收益 − 板块120日收益
- 篮子: 前 3 名等权; 对照组: 后 3 名; 基准: 板块指数

⚠️ 幸存者偏差: meta 股票池是当前精选的 251 只(已退化/退市个股不在池内),
   龙头超额在本检验中大概率被高估——结论只用于"是否值得引入个股层"的方向判断。
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
from backtest_swing import load_inputs  # noqa: E402
from reversal_study import build_rules  # noqa: E402

HORIZONS = [60, 120, 250]
MIN_HIST = 250


def load_stock_panel(pairs, cal):
    """个股收盘价面板(日历 reindex + ffill)。"""
    cols = {}
    have = []
    for code, sname, sector in pairs:
        fp = os.path.join(config.DATA_DIR, f"stock_{code}.csv")
        if not os.path.exists(fp):
            continue
        try:
            df = pd.read_csv(fp, usecols=["date", "close", "amount"])
        except Exception:
            continue
        s = df.set_index(pd.to_datetime(df["date"]))["close"].sort_index()
        s = s[~s.index.duplicated()]
        cols[code] = (s, sector)
        have.append(code)
    panel = pd.DataFrame({c: v[0] for c, v in cols.items()}).reindex(cal).ffill()
    sec_of = {c: v[1] for c, v in cols.items()}
    return panel, sec_of


def main():
    t0 = time.time()
    obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
    meta, dfs = obj["meta"], obj["dfs"]
    inp = load_inputs()
    rules = build_rules(inp)
    cal, sectors = inp["cal"], inp["sectors"]
    base = rules["entry"]["E0"]          # E0 信号: 0→1 转换日

    pairs = sorted({(str(code).zfill(6), sname, name)
                    for name, m in meta.items() for code, sname in m.get("stocks", [])})
    panel, sec_of = load_stock_panel(pairs, cal)
    print(f"[data] 个股面板: {panel.shape[1]}/{len(pairs)} 只有效", flush=True)

    ret250 = panel.pct_change(250)
    ret120 = panel.pct_change(120)
    ret60 = panel.pct_change(60)
    listed = panel.notna().cumsum() >= MIN_HIST   # 上市满 250 交易日

    idx_close = pd.DataFrame(inp["arr"]["close"], index=cal, columns=sectors)
    idx_r60 = idx_close.pct_change(60)
    idx_r120 = idx_close.pct_change(120)

    rows = []
    for j, sec in enumerate(sectors):
        prev = np.concatenate([[0.0], base[:-1, j]])
        sig = np.where((base[:, j] > 0) & (prev == 0))[0]
        pool = [c for c in panel.columns if sec_of.get(c) == sec]
        for d in sig:
            dt = cal[d]
            elig = [c for c in pool if listed.iloc[d][c] and panel.iloc[d][c] > 0]
            if len(elig) < 5:
                continue
            e60 = (ret60.iloc[d][elig] - idx_r60.iloc[d][sec]).astype(float)
            a120 = (ret120.iloc[d][elig] - idx_r120.iloc[d][sec]).astype(float)
            scored = pd.DataFrame({"e60": e60, "a120": a120}).dropna()
            if len(scored) < 5:
                continue
            scored = scored.sort_values(["e60", "a120"], ascending=False)
            top3 = list(scored.index[:3])
            bot3 = list(scored.index[-3:])
            row = {"sector": sec, "date": dt, "n_pool": len(scored)}
            for h in HORIZONS:
                if d + h >= len(cal):
                    continue
                fidx = float(idx_close.iloc[d + h][sec] / idx_close.iloc[d][sec] - 1.0)
                ftop = float(np.mean([panel.iloc[d + h][c] / panel.iloc[d][c] - 1.0
                                      for c in top3]))
                fbot = float(np.mean([panel.iloc[d + h][c] / panel.iloc[d][c] - 1.0
                                      for c in bot3]))
                row[f"idx_{h}"] = fidx
                row[f"top_{h}"] = ftop
                row[f"bot_{h}"] = fbot
                row[f"ex_{h}"] = ftop - fidx
                row[f"spread_{h}"] = ftop - fbot
            rows.append(row)

    df = pd.DataFrame(rows)
    print(f"[events] 信号事件: {len(df)} 个 (板块×日期)", flush=True)
    lines = ["# 龙头股前向检验（板块深寒信号日选股）\n",
             f"- 生成: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} | 事件 {len(df)} 个 | "
             f"龙头=60日板块内超额动量前3名(等权) | 全部指标 ex-ante 无前视",
             "- ⚠️ 幸存者偏差: 股票池为当前精选 251 只, 退市/退化股不在池内 → 龙头超额大概率被**高估**, "
             "本检验只回答\"是否值得引入个股层\"的方向问题\n",
             "| 持有期 | 龙头篮子均值 | 板块指数均值 | 超额均值 | 超额胜率 | 龙3-熊3价差 | 事件数 |",
             "|---|---|---|---|---|---|---|"]
    for h in HORIZONS:
        sub = df.dropna(subset=[f"top_{h}"])
        if not len(sub):
            continue
        ex = sub[f"ex_{h}"]
        lines.append(f"| {h}日 | {sub['top_' + str(h)].mean() * 100:+.1f}% | "
                     f"{sub['idx_' + str(h)].mean() * 100:+.1f}% | **{ex.mean() * 100:+.1f}%** | "
                     f"{(ex > 0).mean() * 100:.0f}% | "
                     f"{sub['spread_' + str(h)].mean() * 100:+.1f}% | {len(sub)} |")

    # 分时期稳定性
    lines += ["", "## 分时期稳定性（250 日持有, 超额均值/胜率）\n",
              "| 时期 | 超额均值 | 胜率 | 事件数 |", "|---|---|---|---|"]
    sub = df.dropna(subset=["top_250"])
    for lo, lo_d, hi_d in [("2010-2015", "2010", "2016"), ("2016-2020", "2016", "2021"),
                           ("2021-2026", "2021", "2027")]:
        s2 = sub[(sub["date"] >= pd.Timestamp(lo_d)) &
                 (sub["date"] < pd.Timestamp(hi_d))]
        if len(s2):
            lines.append(f"| {lo} | {s2['ex_250'].mean() * 100:+.1f}% | "
                         f"{(s2['ex_250'] > 0).mean() * 100:.0f}% | {len(s2)} |")

    # 分板块
    lines += ["", "## 分板块（250 日持有, 事件≥3 次）\n",
              "| 板块 | 事件 | 超额均值 | 胜率 |", "|---|---|---|---|"]
    g = sub.groupby("sector")["ex_250"].agg(["count", "mean",
                                             lambda x: (x > 0).mean()])
    g = g[g["count"] >= 3].sort_values("mean", ascending=False)
    for sec, r in g.iterrows():
        lines.append(f"| {sec} | {int(r['count'])} | {r['mean'] * 100:+.1f}% | "
                     f"{r['<lambda_0>'] * 100:.0f}% |")

    df.to_csv(os.path.join(config.OUTPUT_DIR, "leader_events.csv"),
              index=False, encoding="utf-8-sig")
    with open(os.path.join(config.OUTPUT_DIR, "leader_study.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[done] output/leader_study.md + leader_events.csv | {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
