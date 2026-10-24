# -*- coding: utf-8 -*-
"""wait_to_0831 — 睡到 08:31 直接跑低频链晨补探(一键脚本), 结果落 stdout.log."""
import datetime
import subprocess
import time

while datetime.datetime.now().strftime("%H:%M") < "08:31":
    time.sleep(120)

r = subprocess.run(
    ["python", "-X", "utf8", "v2_b4_morning.py"],
    cwd=r"E:\mystockagent", capture_output=True, text=True, timeout=180)
print("MORNING_PROBE_0831 rc=", r.returncode)
print(r.stdout[-300:])
if r.stderr:
    print("ERR:", r.stderr[-200:])
