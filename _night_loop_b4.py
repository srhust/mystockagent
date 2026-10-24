# -*- coding: utf-8 -*-
"""_night_loop_b4 — 今晚双探自环: 22:30主探→拒→23:55补探→拒则明日交晨环脚本."""
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


while datetime.datetime.now().strftime("%H:%M") < "22:30":
    time.sleep(120)
rc = probe_launch("NIGHT2230")
if rc == 0:
    print("LAUNCHED at 2230")
    raise SystemExit(0)
while datetime.datetime.now().strftime("%H:%M") < "23:55":
    time.sleep(120)
rc2 = probe_launch("NIGHT2355")
print("FINAL rc=", rc2, "(0=launched / 3=refused->明日06:00晨环脚本 _wait_0600 需已排)")
