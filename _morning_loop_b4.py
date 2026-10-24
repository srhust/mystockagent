# -*- coding: utf-8 -*-
"""_morning_loop_b4 — 明日晨双探: 06:00主探→拒→08:30补探. 每天一实例自续."""
import datetime
import subprocess
import time


def probe_launch(tag):
    r = subprocess.run(
        ["python", "-X", "utf8", "v2_b4_morning.py"],
        cwd=r"E:\mystockagent", capture_output=True, text=True, timeout=240)
    line = f"{tag} rc={r.returncode} | {r.stdout.strip()[-160:]}"
    with open(r"E:\mystockagent\data\ashare_history_2000\QC\b4_window_log.txt",
              "a", encoding="utf-8") as f:
        f.write(datetime.datetime.now().isoformat(timespec="seconds") + " " + line + "\n")
    return r.returncode


while datetime.datetime.now().strftime("%H:%M") < "06:00":
    time.sleep(120)
rc = probe_launch("MORN0600")
if rc == 0:
    print("LAUNCHED morning")
    raise SystemExit(0)
while datetime.datetime.now().strftime("%H:%M") < "08:30":
    time.sleep(120)
rc2 = probe_launch("MORN0830")
print("FINAL rc=", rc2)
