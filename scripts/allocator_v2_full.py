# -*- coding: utf-8 -*-
"""审查十六轮: ①补拉8标的SEC XBRL季度营收(KLAC/ASML/TER/FCX/CAT/TSM/STX/WDC, TSM为20-F表型)
②全池双引擎重评(Fast-V全部带fund过滤) ③分配器正式接权: 权重∝S2评分, 单标的上限60%。"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features, build_industry_features

D = os.path.join(config.DATA_DIR, "mu")
CIK = {"KLAC": "0000319201", "ASML": "0000937966", "TER": "0000097210", "FCX": "0000831259",
       "CAT": "0000018230", "TSM": "0001046179"}


def xbrl_fetch(sym, cik):
    u = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/RevenueFromContractWithCustomerExcludingAssessedTax.json"
    req = urllib.request.Request(u, headers={"User-Agent": "research mystockagent@outlook.com"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError:
        u = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/Revenues.json"
        d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "research mystockagent@outlook.com"}), timeout=30))
    units = d["units"].get("USD") or d["units"].get("EUR")
    forms = ("10-Q", "10-K", "20-F")
    rows = [(x["end"], x["val"], x.get("filed")) for x in units
            if x.get("form") in forms and x.get("start") and (pd.Timestamp(x["end"]) - pd.Timestamp(x["start"])).days < 100]
    return pd.DataFrame(rows, columns=["end", "val", "filed"]).drop_duplicates("end").sort_values("filed").drop_duplicates("filed", keep="last")


for sym, cik in CIK.items():
    fp = os.path.join(D, f"{sym.lower()}_fund.csv")
    if os.path.exists(fp) and os.path.getsize(fp) > 100:
        print(f"[cache] {sym}_fund")
        continue
    try:
        df = xbrl_fetch(sym, cik)
        df.to_csv(fp, index=False)
        print(f"[OK] {sym}_fund: {len(df)}季")
    except Exception as e:
        print(f"[FAIL] {sym}: {type(e).__name__} {str(e)[:40]}")

# ② 全池双引擎重评(fund过滤全员)
POOL = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]
rows = []
for sym in POOL:
    c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
    f = build_features(c, [])
    dd = f["dd252"]
    ma10, ma20, ma50 = c.rolling(10).mean(), c.rolling(20).mean(), c.rolling(50).mean()
    vol20 = c.pct_change().rolling(20).std()
    state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
    pathA = ((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)).values
    pathB = ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)).values
    pos = pd.Series((pathA | pathB).astype(float), index=c.index).rolling(120, min_periods=1).max().astype(float).shift(1).fillna(0.0)
    ret = c.pct_change().fillna(0.0)
    eq2 = 100.0 * (1 + pos * ret).cumprod()
    m2 = float((eq2 / eq2.cummax() - 1).min())
    fpath = os.path.join(D, f"{sym.lower()}_fund.csv")
    if os.path.exists(fpath) and os.path.getsize(fpath) > 100:
        ind = build_industry_features(fpath, c.index)
        fund_ok = (ind["rev_yoy"] > -0.30).values
        ftag = ""
    else:
        fund_ok = np.ones(len(c), bool)
        ftag = "(无fund)"
    vel = (dd.diff(10) <= -0.15).values
    n = len(c)
    pxs = c.values
    cash, shares, in_pos, pend = 100.0, 0.0, False, None
    entry_px = stop_px = np.nan
    entry_idx, crash_start, last_end = -1, None, -999
    eqf = pd.Series(np.nan, index=c.index)
    for i in range(1, n):
        px = pxs[i]
        if pend is not None:
            kind, d = pend
            pdx = pxs[d]
            if kind == "buy":
                shares = cash * (1 - 0.0015) / pdx
                entry_px, entry_idx = pdx, d
                stop_px = pdx * (1 - 3 * float(vol20.iloc[d]))
                cash = 0.0
                in_pos = True
            else:
                cash = shares * pdx * (1 - 0.0015)
                shares = 0.0
                in_pos = False
                last_end = d
                crash_start = None
            pend = None
        if vel[i] and not in_pos and pend is None and crash_start is None and i - last_end > 90:
            crash_start = i
        if crash_start is not None and not in_pos and pend is None and i - crash_start <= 15:
            if px > ma10.iloc[i] and i - crash_start >= 2 and fund_ok[i]:
                pend = ("buy", i + 1)
        elif crash_start is not None and not in_pos and i - crash_start > 15:
            crash_start = None
        if in_pos and pend is None and (px / entry_px - 1 >= 0.15 or px <= stop_px or i - entry_idx >= 60):
            pend = ("sell", i + 1)
        eqf.iloc[i] = cash + shares * px
    eqf = eqf.ffill().fillna(100.0)
    mf = float((eqf / eqf.cummax() - 1).min())
    finf = float(eqf.iloc[-1])
    fin2 = float(eq2.iloc[-1])
    fvs = finf / abs(mf) if mf else finf
    rows.append(dict(sym=sym, S2_final=round(fin2, 1), S2_mdd=f"{m2:.0%}", S2_score=round(fin2 / abs(m2)),
                     FV_final=round(finf, 1), FV_mdd=f"{mf:.0%}", FV_score=round(fvs), note=ftag))
    print(f"{sym}: S2评分{fin2/abs(m2):.0f} | FastV评分{fvs:.0f}{ftag}")
d = pd.DataFrame(rows)
d["combo"] = (d.S2_score + d.FV_score).astype(int)
d = d.sort_values("combo", ascending=False)
d.to_csv(os.path.join(D, "..", "allocator_scores_v4.csv"), index=False)

# ③ 分配器权重: 组合评分∝S2+FastV, 单标的≤60%, 起评门槛=组合分≥1000(排除FCX类)
elig = d[(d.combo >= 1000)].copy()
elig["w_raw"] = elig.combo / elig.combo.sum()
elig["w"] = elig.w_raw.clip(upper=0.60)
elig["w"] = elig.w / elig.w.sum()
print("\n=== 分配器v2正式权重(组合评分, 60%单标的上限) ===")
for _, r in elig.iterrows():
    print(f"{r.sym}: 组合分{r.combo} 权重{r.w:.0%}")
print(f"被门槛排除: {d[d.combo < 1000].sym.tolist()}")
