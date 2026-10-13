# -*- coding: utf-8 -*-
"""美股典型周期股温度系统验证: temp<12进 / >75出(85变体), 与A股同构对比。"""
import os
import time

import akshare as ak
import numpy as np
import pandas as pd

DATA = r"E:\mystockagent\data"
OUT = r"E:\mystockagent\output\us_cycle.md"
TICKS = {
    "XLE": "能源ETF", "XME": "金属矿业ETF", "COPX": "铜矿ETF", "GDX": "金矿ETF",
    "XLF": "金融ETF", "FCX": "自由港铜", "NUE": "纽柯钢", "X": "美国钢铁",
    "CLF": "克利夫兰", "AA": "美铝", "MOS": "美盛化肥", "NEM": "纽蒙特金",
    "CAT": "卡特彼勒", "DE": "迪尔", "MU": "美光",
}
CAT = {"能源ETF": "能源", "金属矿业ETF": "金属", "铜矿ETF": "金属", "金矿ETF": "贵金属",
       "金融ETF": "金融", "自由港铜": "金属", "纽柯钢": "金属", "美国钢铁": "金属",
       "克利夫兰": "金属", "美铝": "金属", "美盛化肥": "农业", "纽蒙特金": "贵金属",
       "卡特彼勒": "工业", "迪尔": "工业", "美光": "半导体"}


def _em_map():
    for k in range(3):
        try:
            spot = ak.stock_us_spot_em()
            m = {}
            for _, r in spot.iterrows():
                code = str(r["代码"])
                sym = code.split(".", 1)[1] if "." in code else code
                m[sym] = code
            return m
        except Exception as e:
            print(f"spot retry {k}: {e}", flush=True)
            time.sleep(2)
    return {}


USMAP = _em_map()
print(f"[map] {len(USMAP)} symbols", flush=True)


def load(sym):
    fp = os.path.join(DATA, f"us_{sym}.csv")
    if os.path.exists(fp):
        df = pd.read_csv(fp, parse_dates=["date"])
        if (pd.Timestamp.now() - df["date"].max()).days <= 7 and len(df) > 800:
            return df
    df = None
    for k in range(3):
        try:
            raw = ak.stock_us_daily(symbol=sym)  # 不复权: 新浪qfq因子损坏, 原始价真实
            df = pd.DataFrame({"date": raw["date"], "close": raw["close"]})
            break
        except Exception as e:
            print(f"retry {sym} {k}: {e}", flush=True)
            time.sleep(2)
    if df is None:
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df = df[df["close"] > 0]
    if sym == "CLF":  # 2021-08-10 1:10并股: 前复权手工修正
        df.loc[df["date"] < "2021-08-10", "close"] *= 0.1
    r = df["close"].pct_change().abs()
    df = df[(r.fillna(0) <= 0.60)].reset_index(drop=True)
    df.to_csv(fp, index=False)
    return df


def temp_series(df):
    c = df.set_index("date")["close"].astype(float)
    c = c[c > 0]
    lo = c.rolling(1250, min_periods=500).min()
    hi = c.rolling(1250, min_periods=500).max()
    pp = (c - lo) / (hi - lo)
    r120 = c.pct_change(120).rolling(1250, min_periods=500).rank(pct=True)
    r60 = c.pct_change(60).rolling(1250, min_periods=500).rank(pct=True)
    t = 100 * (0.60 * pp + 0.25 * r120 + 0.15 * r60)
    return c, t


def trades(c, t, enter=12.0, exit_=75.0, min_hold=5):
    tv, cv = t.values.astype(float), c.values
    n = len(tv)
    out = []
    i = 0
    while i < n:
        if np.isfinite(tv[i]) and tv[i] < enter:
            j = i
            while j < n:
                if np.isfinite(tv[j]) and tv[j] > exit_:
                    break
                j += 1
            if j < n and j - i >= min_hold:
                out.append({"days": j - i, "ret": cv[j] / cv[i] - 1.0})
            i = j + 1
        else:
            i += 1
    return out


res, fails = [], []
for sym, name in TICKS.items():
    df = load(sym)
    if df is None or len(df) < 800:
        fails.append(sym)
        continue
    c, t = temp_series(df)
    c, t = c[c.index >= "2010-01-01"], t[t.index >= "2010-01-01"]
    tr = trades(c, t)
    tr85 = trades(c, t, exit_=85.0)
    if not tr:
        fails.append(sym)
        continue
    d = pd.DataFrame(tr)
    d85 = pd.DataFrame(tr85)
    res.append({"sym": sym, "name": name, "cat": CAT[name], "n": len(d),
                "mean": d["ret"].mean(), "med": d["ret"].median(),
                "win": (d["ret"] > 0).mean(), "days": d["days"].mean(),
                "mean85": d85["ret"].mean() if len(d85) else np.nan,
                "win85": (d85["ret"] > 0).mean() if len(d85) else np.nan})
    print(f"OK {sym} {name}: n={len(d)} mean={d['ret'].mean() * 100:+.1f}% win={(d['ret'] > 0).mean() * 100:.0f}%", flush=True)
    time.sleep(0.3)

L = ["# 美股典型周期股温度系统验证 (2010-2026)", "",
     "温度退化版: 价格位置60%+双动量40%(无PB/广度) | 规则: temp<12进, >75出(85变体)", "",
     "| 代码 | 名称 | 类别 | 笔数 | 单笔均值 | 中位 | 胜率 | 平均持仓天 | 85出场均值 | 85胜率 |",
     "|---|---|---|---|---|---|---|---|---|---|"]
D = pd.DataFrame(res)
for r in D.itertuples():
    L.append(f"| {r.sym} | {r.name} | {r.cat} | {r.n} | {r.mean * 100:+.1f}% | {r.med * 100:+.1f}% | "
             f"{r.win * 100:.0f}% | {r.days:.0f} | {r.mean85 * 100:+.1f}% | {r.win85 * 100:.0f}% |")
L += ["", "## 按类别汇总", "| 类别 | 笔数 | 单笔均值 | 中位 | 胜率 | 平均持仓天 |", "|---|---|---|---|---|---|"]
for cat, g in D.groupby("cat"):
    if len(g) >= 2:
        allret = np.concatenate([np.array([r.mean] * r.n) for r in g.itertuples()])
        L.append(f"| {cat} | {g['n'].sum()} | {g['mean'].mean() * 100:+.1f}% | "
                 f"{g['med'].mean() * 100:+.1f}% | {g['win'].mean() * 100:.0f}% | {g['days'].mean():.0f} |")
L += ["", f"## 全体: n={D['n'].sum()} 单笔均值{D['mean'].mean() * 100:+.1f}% "
          f"胜率{D['win'].mean() * 100:.0f}% 平均持仓{D['days'].mean():.0f}天",
      f"85出场: 单笔均值{D['mean85'].mean() * 100:+.1f}% 胜率{D['win85'].mean() * 100:.0f}%",
      "", f"失败/数据不足: {fails if fails else '无'}"]
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("DONE", flush=True)
