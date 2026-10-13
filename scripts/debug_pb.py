# -*- coding: utf-8 -*-
"""调试 PB 数据管道。"""
import sys

sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

from cycle_system import data  # noqa: E402

pdf = data.get_stock_valuation("601088", "市净率")
print("神华PB:", None if pdf is None else pdf.shape)
if pdf is not None:
    print("cols:", pdf.columns.tolist(), pdf["date"].min(), "->", pdf["date"].max())
    print(pdf.head(3).to_string())

codes = ["601088", "601225", "600188", "000983", "601666", "601699", "600985", "600546", "600348", "601898"]
pbs = {}
for c in codes:
    d = data.get_stock_valuation(c, "市净率")
    n = 0 if d is None else len(d)
    print(c, n)
    if d is not None and len(d) > 100:
        pbs[c] = d
med = data.sector_pb_series(pbs)
if med is None:
    print("板块PB序列: None")
else:
    print("板块PB序列:", len(med), med.index.min(), "->", med.index.max())
    print(med.tail(3))
