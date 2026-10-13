# -*- coding: utf-8 -*-
"""验证行业 ETF 数据源: 候选代码逐一测试 fund_etf_hist_sina 可用性。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

from cycle_system import data  # noqa: F401  NO_PROXY + 超时
import akshare as ak  # noqa: E402
import pandas as pd  # noqa: E402

# 候选: (板块, 候选代码列表)
CANDIDATES = [
    ("煤炭", ["sh515220"]),
    ("钢铁", ["sh515210"]),
    ("工业金属", ["sh512400"]),
    ("能源金属", ["sh516150", "sz159615", "sh562733"]),
    ("小金属", ["sh516150", "sz159713"]),
    ("贵金属", ["sh518880", "sz159934"]),
    ("油气开采", ["sz162411", "sh501018"]),
    ("石油加工", ["sz161129", "sh501018"]),
    ("化学原料", ["sz159870"]),
    ("农化制品", ["sz159870", "sz159825"]),
    ("水泥建材", ["sz159745"]),
    ("玻璃玻纤", ["sz159745"]),
    ("造纸", []),
    ("半导体", ["sh512480", "sz159995"]),
    ("元件", ["sz159997", "sz159995"]),
    ("面板", ["sz159997", "sh512480"]),
    ("消费电子", ["sz159732", "sz159997"]),
    ("养殖", ["sz159865"]),
    ("饲料加工", ["sz159865", "sz159825"]),
    ("种植种业", ["sz159825"]),
    ("航运港口", []),
    ("航空机场", []),
    ("证券", ["sh512000"]),
    ("保险", ["sh512070"]),
    ("房地产", ["sh512200"]),
    ("基建建筑", ["sh516970"]),
    ("工程机械", ["sh516970", "sh516320"]),
    ("汽车整车", ["sh516110", "sz516110"]),
    ("电力", ["sz159611", "sh516150"]),
]

print("=== fund_etf_hist_sina 可用性 ===")
ok_map = {}
for sector, codes in CANDIDATES:
    for sym in codes:
        try:
            df = ak.fund_etf_hist_sina(symbol=sym)
            if df is None or len(df) == 0:
                print(f"  {sector:<6} {sym}: 空数据")
                continue
            d0, d1 = pd.to_datetime(df["date"]).min(), pd.to_datetime(df["date"]).max()
            print(f"  {sector:<6} {sym}: OK {len(df)}行 {d0.date()} ~ {d1.date()} 末值{float(df['close'].iloc[-1]):.3f}")
            ok_map.setdefault(sector, sym)
            break
        except Exception as e:  # noqa: BLE001
            print(f"  {sector:<6} {sym}: FAIL {type(e).__name__} {str(e)[:60]}")

print("\n=== ETF 名称表匹配(fund_etf_category_ths) ===")
try:
    lst = ak.fund_etf_category_ths(symbol="ETF")
    print("列:", list(lst.columns), "| 行数:", len(lst))
    kws = ["煤炭", "钢铁", "有色", "黄金", "稀土", "半导体", "芯片", "电子", "养殖", "畜牧",
           "农业", "券商", "保险", "房地产", "基建", "汽车", "电力", "建材", "化工", "油气"]
    namecol = [c for c in lst.columns if "名称" in c][0]
    codecol = [c for c in lst.columns if "代码" in c][0]
    for kw in kws:
        m = lst[lst[namecol].astype(str).str.contains(kw, na=False)]
        if len(m):
            tops = m.head(4)
            print(f"  [{kw}] " + " | ".join(f"{r[namecol]}({r[codecol]})" for _, r in tops.iterrows()))
except Exception as e:  # noqa: BLE001
    print("FAIL:", type(e).__name__, str(e)[:100])

print("\n=== 最终可用映射 ===")
for sector, sym in ok_map.items():
    print(f"  {sector}: {sym}")
missing = [s for s, codes in CANDIDATES if s not in ok_map]
print("  无ETF候选/失败:", missing)
