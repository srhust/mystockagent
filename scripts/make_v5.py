# -*- coding: utf-8 -*-
"""生成v5: v4 + TSM(9股) + 门控改'未创新低'(yoy > 近8季最低YoY) + MU分年代诊断。"""
src = open(r"E:\mystockagent\scripts\nowcaster_v4.py", encoding="utf-8").read()
src = src.replace('"LRCX": "lrcx.csv", "QCOM": "qcom.csv", "TXN": "txn.csv", "INTC": "intc.csv"}',
                  '"LRCX": "lrcx.csv", "QCOM": "qcom.csv", "TXN": "txn.csv", "INTC": "intc.csv", "TSM": "tsm.csv"}')
old_g = """            return (v[-1] / v[-5] - 1) > (v[-2] / v[-6] - 1)"""
new_g = """            yoy = v[-1] / v[-5] - 1
            hist = [v[i] / v[i - 4] - 1 for i in range(max(4, len(v) - 8), len(v))]
            return yoy > (min(hist) if hist else -9)  # 审查十二轮v5: 未创新低(恶化收窄的软化定义)"""
assert old_g in src
src = src.replace(old_g, new_g)
old_mu = """    hits, leads, miss = 0, [], []"""
new_mu = """    hits, leads, miss = 0, [], []
    if n == "MU":
        by_dec = {}
        for t in d["troughs"]:
            dec = str(d["close"].index[t].year)[:3] + "0s"
            by_dec.setdefault(dec, [0, 0])
            cand0 = [s[0] for s in segs if t - 250 <= s[0] <= t + 60]
            by_dec[dec][0] += 1 if cand0 else 0
            by_dec[dec][1] += 1
        print(f"   MU分年代: {by_dec}")"""
assert old_mu in src
src = src.replace(old_mu, new_mu)
open(r"E:\mystockagent\scripts\nowcaster_v5.py", "w", encoding="utf-8").write(src)
print("v5 written OK")
