# -*- coding: utf-8 -*-
import importlib.util
import pandas as pd
import numpy as np

spec = importlib.util.spec_from_file_location("cv44", r"E:\mystockagent\scripts\cv44_asof.py")
# 只取函数定义: 手动执行到测试段之前
src = open(r"E:\mystockagent\scripts\cv44_asof.py", encoding="utf-8").read()
head = src.split("# ===== T-D")[0]
ns = {"__file__": r"E:\mystockagent\scripts\cv44_asof.py", "__name__": "cv44"}
exec(compile(head, "cv44head", "exec"), ns)
run_contract = ns["run_contract"]

dts4 = pd.date_range("2022-01-03", periods=8, freq="B")
pxA = pd.Series([10, 10, np.nan, 20, 20, 20, 20, 20], index=dts4)
maA = pd.Series([1000.0, 1000.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0], index=dts4)
pxB = pd.Series(10.0, index=dts4)
pxD = pd.Series(10.0, index=dts4)
maB = pd.Series(1000.0, index=dts4)
snap4 = {(d.to_period("M"), s): 1.0 for d in dts4 for s in ("A", "B", "D")}
own4 = {s: pd.Series(np.arange(8), index=dts4) for s in ("A", "B", "D")}
collect = {}
eq4, sh4, _ = run_contract(["A", "B", "D"], snap4, "V0", {"A": pxA, "B": pxB, "D": pxD}, {"A": maA, "B": maB, "D": maB}, {"A": {0}, "B": {2}, "D": {2}}, {"A": pd.Series(True, index=dts4), "B": pd.Series(True, index=dts4), "D": pd.Series(True, index=dts4)}, dts4, own4, collect=collect)
print("FILLS:")
for f in collect["fills"]:
    print(f)
print("FUNNEL:", collect["funnel"])
print("EQ:", [round(float(x), 2) for x in eq4.values])
print("SH:", {k: round(v, 3) for k, v in sh4.items()})
