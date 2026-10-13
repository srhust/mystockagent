# -*- coding: utf-8 -*-
"""审查十二轮: Nowcaster前向记录 — 每次运行输出当日11股规则状态快照(累积成前向验证台账)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import pandas as pd

import config
from bottom_nowcaster import build_features

STOCKS = {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv", "AMAT": "amat.csv", "LRCX": "lrcx.csv",
          "QCOM": "qcom.csv", "TXN": "txn.csv", "INTC": "intc.csv", "KLAC": "klac.csv", "ASML": "asml.csv", "TER": "ter.csv"}
rows = []
for name, fn in STOCKS.items():
    df = pd.read_csv(os.path.join(config.DATA_DIR, "mu", fn), parse_dates=["date"]).sort_values("date").set_index("date")
    c = df["close"].astype(float)
    f = build_features(c, [])
    state = ((f["dd252"] <= -0.35) & (f["pos1250"] <= 0.35)).astype(int)
    rows.append(pd.DataFrame({"stock": name, "dd252": f["dd252"].round(4), "pos1250": f["pos1250"].round(4), "rule_state": state}))
out = pd.concat(rows)
last = out.index.max()
d = out.loc[last]
od = os.path.join(config.DATA_DIR, "nowcaster_daily")
os.makedirs(od, exist_ok=True)
d.to_csv(os.path.join(od, f"state_{last.date()}.csv"))
print(f"前向记录 {last.date()}: 预警中 {list(d[d.rule_state == 1].stock)}")
