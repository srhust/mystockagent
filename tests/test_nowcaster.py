# -*- coding: utf-8 -*-
"""审查十五轮P0自动化测试: 统一匹配器/as-of谷底特征/确认日/快V出场判定。运行: python tests/test_nowcaster.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, r"E:\mystockagent\cycle_system")
sys.path.insert(0, r"E:\mystockagent")
import _path  # noqa: F401

import numpy as np
import pandas as pd

from bottom_nowcaster import build_features, match_events_to_troughs, trough_confirm_idx

ok = []


def check(name, cond):
    ok.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'} {name}")


# 1) days_since_prev_trough 逐日递增(审查十四轮修复的回归测试)
c = pd.Series([10.0] * 20 + list(np.linspace(5, 12, 30)), index=pd.date_range("2020-01-01", periods=50))
f = build_features(c, [20])
check("dist逐日递增", f["days_since_prev_trough"].iloc[21] == 1 and f["days_since_prev_trough"].iloc[25] == 5)
check("dist谷底日为0", f["days_since_prev_trough"].iloc[20] == 0)

# 2) trough_confirm_idx: 合成V型(谷在第10日, +30%反弹确认日)
v = pd.Series([10.0] * 10 + list(np.linspace(10, 4, 10)) + list(np.linspace(4, 6.5, 12)), index=pd.date_range("2021-01-01", periods=32))
conf = trough_confirm_idx(v, [19])
check("确认日=首次+30%反弹", int(conf[0]) > 19 and v.iloc[int(conf[0])] >= 4.0 * 1.3 - 1e-9 and v.iloc[int(conf[0]) - 1] < 4.0 * 1.3)

# 3) as-of: 确认日前谷底特征不可见
f2 = build_features(v, [19], confirm_idx=conf)
check("确认日前dist=中性标记999", f2["days_since_prev_trough"].iloc[19] == 999.0)
check("确认日后dist可见", f2["days_since_prev_trough"].iloc[int(conf[0])] == int(conf[0]) - 19)

# 4) 统一匹配器: 交叉候选取最近, P/R同源
events = [10, 12]
troughs = [11, 30]
m = match_events_to_troughs(events, troughs, max_after=120)
check("最近优先配对", (10, 11, 1) in m and (12, 30, 18) in m and len(m) == 2)
events2 = [10, 11]
troughs2 = [12]
m2 = match_events_to_troughs(events2, troughs2, max_after=120)
check("一谷至多一警(最近优先)", len(m2) == 1 and m2[0][0] == 11)
p = len([1 for _, _, g in m2 if g <= 60]) / len(events2)
r = len([1 for _, _, g in m2 if g <= 60]) / len(troughs2)
check("P/R同源一致", abs(p - 0.5) < 1e-9 and abs(r - 1.0) < 1e-9)

# 5) 快V出场判定(限时 bug 回归)
sys.path.insert(0, r"E:\mystockagent\scripts")
import importlib

fv = importlib.import_module("mu_fastv_v2")
check("限时60日触发", fv.exits(0.0, 100.0, 50.0, 60) is True)
check("止盈触发", fv.exits(0.16, 100.0, 50.0, 5) is True)
check("未触发", fv.exits(0.05, 100.0, 50.0, 30) is False)

print(f"\n{sum(1 for _, x in ok if x)}/{len(ok)} 通过")
if not all(x for _, x in ok):
    sys.exit(1)

