# -*- coding: utf-8 -*-
"""_sentinel_late — 16:20 LATE彩排实体化(若东财当日恢复): fetch 10-19→plan 10-19(LATE)链上如实标注。"""
import subprocess
import sys
import time
from datetime import datetime

TOOLS = "cycle_system/ashare_v2/tools"
LOG = "output/ashare_v2/independent/LIVE_2026-10-20.log"


def run(cmd, tag):
    line = f"\n===== {tag} @ {datetime.now().isoformat(timespec='seconds')}\n$ {cmd}\n"
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=1500,
                           encoding="utf-8", errors="replace")
        rc = p.returncode
        line += f"[exit {rc}]\nSTDOUT:\n{(p.stdout or '')[:3000]}\nSTDERR:\n{(p.stderr or '')[-800:]}"
    except subprocess.TimeoutExpired:
        rc = 99
        line += "[exit 99] SUBPROCESS_TIMEOUT_1500S(有界失败如实入档, 哨兵链继续后续命令)"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line[:1000], flush=True)
    return rc


while True:
    now = datetime.now()
    if (now.hour, now.minute) >= (16, 20):
        break
    time.sleep(60)

with open(LOG, "a", encoding="utf-8") as f:
    f.write(f"LATE SENTINEL START {datetime.now().isoformat(timespec='seconds')}\n")
rc = run(f"python -X utf8 {TOOLS}/forward_shadow.py fetch 2026-10-19", "LATE-FETCH 10-19")
if rc == 0:
    run(f"python -X utf8 {TOOLS}/forward_shadow.py plan 2026-10-19", "LATE-PLAN 10-19 (预期LATE_RECONSTRUCTION)")
    run(f"python -X utf8 {TOOLS}/forward_shadow.py verify", "CHAIN VERIFY(2)")
else:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("LATE rehearsal skipped: EM fetch blocked (gate correct, no fabrication)\n")
