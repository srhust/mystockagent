# -*- coding: utf-8 -*-
"""数据源核对: 现有缓存 vs 同花顺(THS)新取 + 期货(新浪)重取, 输出差异报告。"""
import os
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import akshare as ak  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402

warnings.filterwarnings("ignore")
obj = pd.read_pickle(os.path.join(config.DATA_DIR, "enriched.pkl"))
dfs, meta = obj["dfs"], obj["meta"]
L = ["# 数据源核对报告（缓存 vs THS/新浪重取）", f"- 生成: {pd.Timestamp.now():%Y-%m-%d %H:%M}", ""]


def norm_close(df, date_col=("日期", "date"), close_col=("收盘价", "收盘", "close")):
    for dc in date_col:
        if dc in df.columns:
            for cc in close_col:
                if cc in df.columns:
                    s = pd.Series(pd.to_numeric(df[cc], errors="coerce").values,
                                  index=pd.to_datetime(pd.to_datetime(df[dc]).dt.date))
                    return s.groupby(level=0).last().sort_index()
    return None


def compare(name, cache_s, fresh_s, n_tail=250):
    if fresh_s is None or len(fresh_s) < 100:
        return "FETCH-FAIL(数据不足)"
    a, b = cache_s.dropna(), fresh_s.dropna()
    j = a.index.intersection(b.index)
    if len(j) < 100:
        return f"日期交集不足({len(j)}天)"
    tail = j[-n_tail:]
    ra, rb = a.loc[tail].pct_change(), b.loc[tail].pct_change()
    d = (ra - rb).abs().dropna()
    big = (d > 0.01).sum()
    px = (a.loc[tail] / b.loc[tail] - 1).abs().max()
    tag = "OK" if big <= 2 and px < 0.02 else f"MISMATCH"
    return f"{tag}: 收盘偏差max={px * 100:.2f}% 日收益差异>1%天数={big}/{len(tail)}"


# 1) 行业指数: THS 重取
ok_n = mm = ff = 0
for name, m in meta.items():
    cache_s = dfs[name]["close"]
    cache_s.index = pd.to_datetime(cache_s.index)
    fresh = None
    for k in range(2):
        try:
            raw = ak.stock_board_industry_index_ths(symbol=m["ths"], start_date="20100101",
                                                    end_date="20261012")
            fresh = norm_close(raw)
            break
        except Exception as e:
            if k == 1:
                L.append(f"- {name}: THS重取失败 {type(e).__name__} {str(e)[:60]}")
                ff += 1
            time.sleep(1.5)
    if fresh is None:
        continue
    v = compare(name, cache_s, fresh)
    if v.startswith("OK"):
        ok_n += 1
    elif v.startswith("MISMATCH"):
        mm += 1
        L.append(f"- **{name}** {v}")
    else:
        ff += 1
        L.append(f"- {name} {v}")
    time.sleep(0.4)
L.insert(4, f"## 一、行业指数(THS重取): OK {ok_n} / MISMATCH {mm} / 失败 {ff}\n")

# 2) 期货: 新浪重取
futs = sorted({m.get("fut") for m in meta.values() if m.get("fut")})
L.append(f"\n## 二、期货({len(futs)}个, 新浪重取)")
for code in futs:
    fp = os.path.join(config.DATA_DIR, f"fut_{code}.csv")
    try:
        raw = ak.futures_main_sina(symbol=code, start_date="20100101", end_date="20261012")
        fresh = norm_close(raw)
        cache = pd.read_csv(fp, parse_dates=["date"]) if os.path.exists(fp) else None
        cache_s = pd.Series(cache["close"].values, index=pd.to_datetime(cache["date"])) if cache is not None else None
        L.append(f"- {code}: {compare(code, cache_s, fresh)}")
        time.sleep(0.5)
    except Exception as e:
        L.append(f"- {code}: 重取失败 {type(e).__name__} {str(e)[:50]}")

L.append("\n> 东财(EM)当前对本机硬断连(已两次复测), 东财系核对待网络修复后执行; "
         "本次核对覆盖: 行业指数=THS源, 期货=新浪源, 美股=新浪原始价(今日已重建)。")
with open(os.path.join(config.OUTPUT_DIR, "source_audit.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("DONE", flush=True)
