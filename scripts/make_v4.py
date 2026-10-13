# -*- coding: utf-8 -*-
"""生成v4: v3 + 8股池 + 无fund股票守卫 + 产业确认门控。"""
src = open(r"E:\mystockagent\scripts\nowcaster_v3.py", encoding="utf-8").read()
src = src.replace('STOCKS = {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv"}',
                  'STOCKS = {"MU": "MU.csv", "STX": "stx.csv", "WDC": "wdc.csv", "AMAT": "amat.csv", '
                  '"LRCX": "lrcx.csv", "QCOM": "qcom.csv", "TXN": "txn.csv", "INTC": "intc.csv"}')
old_load = '''    fund = pd.read_csv(os.path.join(config.DATA_DIR, "mu", f"{name.lower()}_fund.csv"), parse_dates=["end", "filed"]).dropna(subset=["filed"])'''
new_load = '''    fund_fp0 = os.path.join(config.DATA_DIR, "mu", f"{name.lower()}_fund.csv")
    if not os.path.exists(fund_fp0):
        DATA[name] = dict(close=c, troughs=qual, ind=None)
        continue
    fund = pd.read_csv(fund_fp0, parse_dates=["end", "filed"]).dropna(subset=["filed"])'''
assert old_load in src, "load anchor missing"
src = src.replace(old_load, new_load)
old_join = '''    f = build_features(d["close"], d["troughs"]).join(d["ind"], how="left")'''
new_join = '''    f = build_features(d["close"], d["troughs"]).join(d["ind"], how="left") if d.get("ind") is not None else build_features(d["close"], d["troughs"])'''
assert old_join in src, "join anchor missing"
src = src.replace(old_join, new_join)
old_fa = '''    fa = [s for s in segs if not any(k in zone for k in s)]
'''
gate = '''    fa = [s for s in segs if not any(k in zone for k in s)]
    fund_fp = os.path.join(config.DATA_DIR, "mu", f"{n.lower()}_fund.csv")
    if os.path.exists(fund_fp):
        fd = pd.read_csv(fund_fp, parse_dates=["end", "filed"]).dropna(subset=["filed"]).sort_values("filed")

        def ind_ok(seg):
            t_seg = d["close"].index[seg[0]]
            vis = fd[fd["filed"] <= t_seg]
            if len(vis) < 6:
                return None
            v = vis["val"].values
            if min(v[-1], v[-5], v[-2], v[-6]) <= 0:
                return None
            return (v[-1] / v[-5] - 1) > (v[-2] / v[-6] - 1)

        conf = [s for s in segs if ind_ok(s) is True]
        fa_conf = [s for s in fa if ind_ok(s) is True]
        print(f"   产业确认门控: 确认预警{len(conf)} 确认后假警{len(fa_conf)}")
'''
assert old_fa in src, "fa anchor missing"
src = src.replace(old_fa, gate, 1)
open(r"E:\mystockagent\scripts\nowcaster_v4.py", "w", encoding="utf-8").write(src)
print("v4 written OK")
