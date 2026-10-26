# -*- coding: utf-8 -*-
"""_night_loop_b4 — 今晚双探自环(日期感知修正版): 22:30→拒→23:55→交晨环."""
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


def wait_until(hhmm):
    now = datetime.datetime.now()
    due = now.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:]), second=0, microsecond=0)
    if due <= now:
        due = due + datetime.timedelta(days=1)
    while datetime.datetime.now() < due:
        time.sleep(min(600, max(10, (due - datetime.datetime.now()).total_seconds())))


wait_until("22:30")
rc = probe_launch("NIGHT2230")
if rc == 0:
    print("LAUNCHED at 2230")
    raise SystemExit(0)
wait_until("23:55")
rc2 = probe_launch("NIGHT2355")
print("FINAL rc=", rc2, "(0=launched / 3=refused→交明晨环; 4=已在跑勿双开)")
