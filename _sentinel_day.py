# -*- coding: utf-8 -*-
"""_sentinel_day — FORWARD 每日哨兵(通用版, 取代_sentinel_1020.py手改日期模式).

晨间启动: python -X utf8 _sentinel_day.py (无参=自动今日)
15:52 单次有界 EM fetch(裁定C"只尝试一次", 零行=闸门如实阻断不重试)→
plan 成=FORWARD首条当日+verify/rehearsal/redline; →C层(19:30窗) sina probe+crosscheck。
run()含超时兜底(subprocess.TimeoutExpired=rc99入档链继续——今日对常驻进程改不了文件的教训: 通用版先固化)。
"""
import subprocess
import sys
import time
from datetime import datetime

TOOLS = "cycle_system/ashare_v2/tools"


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def run(cmd, tag, log):
    line = f"\n===== {tag} @ {datetime.now().isoformat(timespec='seconds')}\n$ {cmd}\n"
    rc = 99
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=2400,
                           encoding="utf-8", errors="replace")
        rc = p.returncode
        line += f"[exit {rc}]\nSTDOUT:\n{(p.stdout or '')[:4000]}\nSTDERR:\n{(p.stderr or '')[-1500:]}"
    except subprocess.TimeoutExpired:
        line += "[exit 99] SUBPROCESS_TIMEOUT_2400S(有界失败如实入档, 哨兵链继续)"
    except Exception as e:
        line += f"EXC {type(e).__name__}: {e}"
    with open(log, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line[:1200], flush=True)
    return rc


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else today()
    log = f"output/ashare_v2/independent/LIVE_{day}.log"
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"SENTINEL-DAY START {day} @ {datetime.now().isoformat(timespec='seconds')}\n")
    while True:
        now = datetime.now()
        if (now.hour, now.minute) >= (15, 52):
            break
        time.sleep(60)
    print(f"window reached {datetime.now()}", flush=True)
    rc = run(f"python -X utf8 {TOOLS}/forward_shadow.py fetch {day}", f"FETCH EM {day}", log)
    if rc == 0:
        run(f"python -X utf8 {TOOLS}/forward_shadow.py plan {day}", f"PLAN {day}", log)
        run(f"python -X utf8 {TOOLS}/forward_shadow.py verify", "CHAIN VERIFY", log)
        run(f"python -X utf8 {TOOLS}/rehearsal_check.py", "REHEARSAL", log)
        run(f"python -X utf8 {TOOLS}/redline_audit.py", "REDLINE", log)
    else:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"EM fetch blocked {day}: gate stands, no retry (ruling C single bounded)\n")
        print("blocked; gate stands.", flush=True)
    # C层 19:30窗(sina日更滞后实证)
    while True:
        now = datetime.now()
        if (now.hour, now.minute) >= (19, 30):
            break
        time.sleep(120)
    run(f"python -X utf8 {TOOLS}/sina_channel_probe.py {day}", "SINA PROBE C-LAYER", log)
    run(f"python -X utf8 {TOOLS}/live_crosscheck.py {day} --codes 000002,000012,000050,000059,000060",
        "BAOSTOCK CROSSCHECK", log)
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"SENTINEL-DAY DONE {datetime.now().isoformat(timespec='seconds')}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
