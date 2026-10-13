# -*- coding: utf-8 -*-
"""快速查看当前各板块周期状态（秒级, 基于本地缓存）。

用法:
    python scripts/status.py             # 用已缓存的富化结果（若数据有更新则自动重建）
    python scripts/status.py --rebuild   # 强制重新富化（约 1-2 分钟）
    python scripts/status.py --refresh   # 先增量更新数据再查看

输出: 温度排序状态表 + 深寒买入观察名单 + 过热预警名单 + 最近阶段变化。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401  (加入 .libs)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import data  # noqa: E402
from cycle_system.cycle_engine import STAGE_CN, enrich_sector  # noqa: E402
from cycle_system import strategy as strat  # noqa: E402

PKL = os.path.join(config.DATA_DIR, "enriched.pkl")


def pickle_is_fresh():
    """富化缓存是否仍然新鲜: 比所有数据 csv 都新。"""
    if not os.path.exists(PKL):
        return False
    pkl_mtime = os.path.getmtime(PKL)
    for f in os.listdir(config.DATA_DIR):
        if f.endswith(".csv") and os.path.getmtime(os.path.join(config.DATA_DIR, f)) > pkl_mtime:
            return False
    return True


def build(sectors=None):
    """重建富化数据（复用 main.build_enriched 的逻辑但更安静）。"""
    sectors = sectors or list(config.CYCLE_SECTORS.keys())
    bench = data.get_benchmark()
    if bench is None or len(bench) < 250:
        print("错误: 无基准数据, 请先运行 scripts/fetch_data.py")
        sys.exit(1)
    bench_close = bench.set_index("date")["close"]
    dfs, meta = {}, {}
    for i, name in enumerate(sectors, 1):
        if name not in config.CYCLE_SECTORS:
            continue
        m = config.CYCLE_SECTORS[name]
        idx = data.get_industry_hist(m["ths"])
        if idx is None or len(idx) < 300:
            continue
        stocks = {}
        for code, cname in m["stocks"][:config.BREADTH_STOCK_LIMIT]:
            sdf = data.get_stock_hist(code)
            if sdf is not None and len(sdf) > 250:
                stocks[code] = sdf
        stock_closes = data.sector_stock_closes(stocks) if stocks else None
        pbs = {}
        for code, cname in m["stocks"][:config.PB_STOCK_LIMIT]:
            pdf = data.get_stock_valuation(code, "市净率")
            if pdf is not None and len(pdf) > 100:
                pbs[code] = pdf
        pb_series = data.sector_pb_series(pbs) if pbs else None
        dfs[name] = enrich_sector(idx, bench_close, stock_closes=stock_closes, pb_series=pb_series)
        meta[name] = m
        print(f"  [{i}/{len(sectors)}] {name}: 温度{dfs[name]['temp'].iloc[-1]:.0f} "
              f"阶段={STAGE_CN.get(dfs[name]['stage'].iloc[-1], '?')}")
    pd.to_pickle({"dfs": dfs, "meta": meta, "ts": pd.Timestamp.now()}, PKL)
    return dfs, meta


def load_or_build(force=False):
    if not force and pickle_is_fresh():
        obj = pd.read_pickle(PKL)
        print(f"[status] 使用富化缓存 ({obj['ts']})")
        return obj["dfs"], obj["meta"]
    print("[status] 重建富化数据 ...")
    return build()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="强制重新富化")
    ap.add_argument("--refresh", action="store_true", help="先增量更新数据")
    args = ap.parse_args()

    if args.refresh:
        print("[status] 增量更新数据 ...")
        data.load_all()

    dfs, meta = load_or_build(force=args.rebuild)

    rows = []
    for name, df in dfs.items():
        last = df.iloc[-1]
        pos = strat.position_target(df, mode="hybrid").iloc[-1]
        rows.append({
            "板块": name, "类别": meta.get(name, {}).get("cat", ""),
            "温度": round(float(last["temp"])),
            "阶段": STAGE_CN.get(last["stage"], last["stage"]),
            "PB分位": f"{last['pb_pct'] * 100:.0f}%" if np.isfinite(last.get("pb_pct", np.nan)) else "-",
            "距高点": f"{last['dd_from_high'] * 100:.0f}%" if np.isfinite(last.get("dd_from_high", np.nan)) else "-",
            "温度60日Δ": f"{last['temp'] - df['temp'].iloc[-min(len(df), 60)]:+.0f}",
            "信号": "持有" if pos == 1 else "空仓",
        })
    t = pd.DataFrame(rows).sort_values("温度", ascending=False)
    print("\n" + "=" * 78)
    print(f"当前周期状态（数据截至 {max(df.index[-1] for df in dfs.values()).date()}，按温度降序）")
    print("=" * 78)
    print(t.to_string(index=False))

    # 深寒买入观察名单
    cold = t[t["温度"] < 20]
    if len(cold):
        print("\n>>> 深寒买入观察名单（温度<20, 实证120日胜率67-84%）:")
        for _, r in cold.iterrows():
            print(f"    {r['板块']:<6} 温度{r['温度']:>3}  PB分位{r['PB分位']:>5}  {r['阶段']}")

    # 过热预警名单
    hot = t[(t["温度"] >= 75) | (t["阶段"] == "见顶")]
    if len(hot):
        print("\n>>> 过热预警名单（温度>=75 或 见顶, 逢高分批兑现）:")
        for _, r in hot.iterrows():
            print(f"    {r['板块']:<6} 温度{r['温度']:>3}  PB分位{r['PB分位']:>5}  {r['阶段']}")

    # 最近90天的阶段变化
    print("\n>>> 最近 90 天阶段变化（周期动向）:")
    cutoff = max(df.index[-1] for df in dfs.values()) - pd.Timedelta(days=90)
    n_chg = 0
    for name, df in dfs.items():
        st = df["stage"]
        chg = st[st != st.shift()]
        chg = chg[chg.index > cutoff]
        if len(chg) > 0:
            for d, s in chg.items():
                prev_s = st.loc[:d].iloc[:-1].iloc[-1] if d != st.index[0] else "start"
                print(f"    {d.date()}  {name:<6}: {STAGE_CN.get(prev_s, prev_s)} -> {STAGE_CN.get(s, s)}")
                n_chg += 1
    if n_chg == 0:
        print("    （无变化）")


if __name__ == "__main__":
    main()
