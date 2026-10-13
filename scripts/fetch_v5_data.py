# -*- coding: utf-8 -*-
"""审查十二轮v5: ①TSM补拉 ②同业SEC产业数据批量(免费XBRL) ③门控改'未创新低'定义 ④MU分年代诊断。"""
import json
import os
import urllib.request

import pandas as pd

# ② 同业SEC产业数据(免费)
CIK = {"AMAT": "0000006951", "LRCX": "0000707549", "QCOM": "0000804328", "TXN": "0000097476", "INTC": "0000050863"}
for name, cik in CIK.items():
    try:
        u = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/RevenueFromContractWithCustomerExcludingAssessedTax.json"
        req = urllib.request.Request(u, headers={"User-Agent": "research mystockagent@outlook.com"})
        d = json.load(urllib.request.urlopen(req, timeout=30))
        rows = [(x["end"], x["val"], x.get("filed")) for x in d["units"]["USD"]
                if x.get("form") in ("10-Q", "10-K") and x.get("start") and (pd.Timestamp(x["end"]) - pd.Timestamp(x["start"])).days < 100]
        df = pd.DataFrame(rows, columns=["end", "val", "filed"]).drop_duplicates("end")
        df.to_csv(rf"E:\mystockagent\data\mu\{name.lower()}_fund.csv", index=False)
        print(f"{name}_fund: {len(df)}季 {df['end'].min()}~{df['end'].max()}")
    except Exception as e:
        print(name, "FAIL", str(e)[:40])

# ① TSM补拉(换前缀)
import akshare as ak
for sym in ("106.TSM", "105.TSM"):
    try:
        df = ak.stock_us_hist(symbol=sym, adjust="qfq").rename(columns={"日期": "date", "收盘": "close"})[["date", "close"]]
        df.to_csv(r"E:\mystockagent\data\mu\tsm.csv", index=False)
        print(f"TSM: {len(df)} {df['date'].iloc[0]}~{df['date'].iloc[-1]}")
        break
    except Exception as e:
        print("TSM", sym, "FAIL", str(e)[:30])
