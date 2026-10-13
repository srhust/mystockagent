# -*- coding: utf-8 -*-
"""生成v8: v7 + 设备组扩池(AMAT/LRCX/KLAC/ASML/TER) + QCOM迁移解构(系数表)。"""
src = open(r"E:\mystockagent\scripts\nowcaster_v7.py", encoding="utf-8").read()
src = src.replace('"LRCX": "lrcx.csv", "QCOM": "qcom.csv", "TXN": "txn.csv", "INTC": "intc.csv"}',
                  '"LRCX": "lrcx.csv", "QCOM": "qcom.csv", "TXN": "txn.csv", "INTC": "intc.csv", '
                  '"KLAC": "klac.csv", "ASML": "asml.csv", "TER": "ter.csv"}')
src = src.replace('"EQP": ["AMAT", "LRCX"]', '"EQP": ["AMAT", "LRCX", "KLAC", "ASML", "TER"]')
old_iso = """            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(p_in[k], y_in.values[k].astype(float))"""
new_iso = """            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(p_in[k], y_in.values[k].astype(float))
            if n == "QCOM" and t == "P_bottom_20d":
                cf = pd.Series(lrg.coef_[0], index=Xg2.columns).sort_values()
                print(f"   QCOM迁移解构(LOOX系数,仅INTC训练): {cf.to_dict()}")"""
assert old_iso in src
src = src.replace(old_iso, new_iso)
open(r"E:\mystockagent\scripts\nowcaster_v8.py", "w", encoding="utf-8").write(src)
print("v8 written OK")
