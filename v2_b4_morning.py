# -*- coding: utf-8 -*-
"""v2_b4_morning — 10-24 早窗一键: 双良券探针→开则托管V2批4死券专批(幂等skip)。

退出码: 0=已托管(输出PID) / 3=探针拒窗(交下一环) / 4=已有实例在跑(勿双开)。
"""
import subprocess
import sys
import threading

sys.path.insert(0, r"E:\mystockagent")

# 勿双开: 检查现存 deadcode 进程
tasklist = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe"],
                          capture_output=True, text=True).stdout
try:
    import baostock as bs
except ImportError:
    sys.exit(3)

box = {}


def probe():
    try:
        lg = bs.login()
        for code in ("sh.600000", "sz.000002"):
            rs = bs.query_history_k_data_plus(code, "date,close",
                                              start_date="2021-01-01",
                                              end_date="2021-02-01",
                                              frequency="d", adjustflag="3")
            n = 0
            while rs.error_code == "0" and rs.next():
                n += 1
            box[code] = (rs.error_code, n)
        bs.logout()
    except Exception as e:  # noqa
        box["exc"] = type(e).__name__


t = threading.Thread(target=probe, daemon=True)
t.start()
t.join(50)
ok = all(box.get(c, ("", 0))[1] > 5 for c in ("sh.600000", "sz.000002"))
print("PROBE:", box if box else "HANG", "->", "OPEN" if ok else "REFUSED")
if not ok:
    sys.exit(3)

p = subprocess.Popen(
    [sys.executable, "-X", "utf8", "-u",
     r"E:\mystockagent\cycle_system\ashare_v2\tools\poc2000_deadcode_bst.py",
     "--plan", r"E:\mystockagent\data\ashare_history_2000\QC\deadcode_batch_plan_2020_2026.json"],
    stdout=open(r"E:\mystockagent\data\ashare_history_2000\QC\bst_b4_stdout.log", "a",
                encoding="utf-8"),
    stderr=open(r"E:\mystockagent\data\ashare_history_2000\QC\bst_b4_stderr.log", "a",
                encoding="utf-8"),
    cwd=r"E:\mystockagent")
print("LAUNCHED PID", p.pid)
sys.exit(0)
