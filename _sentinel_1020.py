# -*- coding: utf-8 -*-
"""_sentinel_1020 — 今日直播自动序列(15:52起, 单轮不轮询).

1) fetch 2026-10-20(16元组东财链路): 通→plan 2026-10-20=首条真FORWARD; 拒→零行闸门如实登记
2) 无论结果: sina全池probe(C层数据事实备份) + baostock crosscheck(2b对账)
3) 若plan成: rehearsal_check + redline_audit
全程输出落 output/ashare_v2/independent/LIVE_2026-10-20.log
"""
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
        line += f"[exit {p.returncode}]\nSTDOUT:\n{(p.stdout or '')[:4000]}\nSTDERR:\n{(p.stderr or '')[-1500:]}"
    except Exception as e:
        line += f"EXC {type(e).__name__}: {e}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line[:1200], flush=True)
    return p.returncode if "p" in dir() else 1


def main():
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"SENTINEL START {datetime.now().isoformat(timespec='seconds')}\n")
    # 等至15:52
    while True:
        now = datetime.now()
        if (now.hour, now.minute) >= (15, 52):
            break
        time.sleep(60)
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"window reached {datetime.now()}", flush=True)
    rc_fetch = run(f"python -X utf8 {TOOLS}/forward_shadow.py fetch 2026-10-20", "FETCH EM 10-20")
    if rc_fetch == 0:
        run(f"python -X utf8 {TOOLS}/forward_shadow.py plan 2026-10-20", "PLAN 10-20 (FORWARD!)")
        run(f"python -X utf8 {TOOLS}/forward_shadow.py verify", "CHAIN VERIFY")
        run(f"python -X utf8 {TOOLS}/rehearsal_check.py", "REHEARSAL H1-H5")
        run(f"python -X utf8 {TOOLS}/redline_audit.py", "REDLINE AUDIT")
    else:
        print("EM fetch blocked; gate stands. Plan skipped (correct).", flush=True)
    # C层备份(无论EM结果)
    run(f"python -X utf8 {TOOLS}/sina_channel_probe.py 2026-10-20", "SINA PROBE C-LAYER")
    run(f"python -X utf8 {TOOLS}/live_crosscheck.py 2026-10-20 --codes 000002,000012,000050,000059,000060",
        "BAOSTOCK CROSSCHECK 2b")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"SENTINEL DONE {datetime.now().isoformat(timespec='seconds')}\n")


if __name__ == "__main__":
    main()
