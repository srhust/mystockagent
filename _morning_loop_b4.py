# -*- coding: utf-8 -*-
"""_morning_loop_b4 v2 — 正确跨天定时: 睡到明天 06:00/08:30."""
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
    due = now.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:]),
                      second=0, microsecond=0)
    if due <= now:                       # 今日时刻已过→明天; 未到→今日 (当日双探语义)
        due = due + datetime.timedelta(days=1)
    while datetime.datetime.now() < due:
        time.sleep(min(600, max(10, (due - datetime.datetime.now()).total_seconds())))


wait_until("06:00")
rc = probe_launch("MORN0600")
if rc == 0:
    print("LAUNCHED morning")
    raise SystemExit(0)
wait_until("08:30")
rc2 = probe_launch("MORN0830")
print("FINAL rc=", rc2)
