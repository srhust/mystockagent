# -*- coding: utf-8 -*-
"""R39: 官方分页v2——raise_for_status+结构校验+每页原子checkpoint+动态count+完整原始响应+失败即退不重试."""
import json
import hashlib
import requests
import urllib3

urllib3.disable_warnings()
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/114.0.0.0 Safari/537.36"}
B = "https://www.swsresearch.com/institute-sw/api/"
STATE_F = r"E:\mystockagent\output\ashare\sws_r37d_resume.json"
PAGE_BUDGET = 3  # 每轮≤3页

try:
    st = json.load(open(STATE_F, encoding="utf-8"))
except Exception:
    st = {"pages_done": [], "raw_pages": {}, "records": [], "status": "INIT", "total_count": None}

def atomic_save():
    tmp = STATE_F + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    import os
    os.replace(tmp, STATE_F)

done = set(st["pages_done"])
new = 0
p = 1
timeout_hit = False
while new < PAGE_BUDGET:
    if p in done:
        p += 1
        if p > 60:
            break
        continue
    try:
        r = requests.get(B + "index_announcements/index/", params={"page": str(p), "page_size": "50"}, headers=H, timeout=30, verify=False)
        r.raise_for_status()  # 失败即退, 不重试
        d = r.json().get("data")
        if not d or "results" not in d:  # 结构校验(HTML错误页/JSON变化)
            st["status"] = f"BAD_STRUCTURE@page{p}"
            atomic_save()
            break
        st["total_count"] = d.get("count", st["total_count"])  # 动态count
        st["raw_pages"][str(p)] = {"sha256": hashlib.sha256(r.text.encode()).hexdigest(), "raw": r.text}  # 完整原始响应
        st["records"] += [{"id": x.get("id"), "declaredate": x.get("declaredate"), "title": x.get("title"), "page": p} for x in d["results"]]
        st["pages_done"] = sorted(done | {p})
        atomic_save()  # 每页原子checkpoint
        done.add(p)
        new += 1
        p += 1
        if st["total_count"] and p * 50 >= st["total_count"] + 50:
            st["status"] = "COMPLETE_PAGED"
            atomic_save()
            break
    except requests.exceptions.Timeout:
        timeout_hit = True
        st["status"] = "BLOCKED_TIMEOUT_SUSPECTED_RATE_LIMIT"
        atomic_save()
        break
    except Exception as ex:
        st["status"] = f"ERROR_{type(ex).__name__}@page{p}"
        atomic_save()
        break
if not timeout_hit and st["status"] not in ("COMPLETE_PAGED",) and "BAD" not in st["status"] and "ERROR" not in st["status"]:
    st["status"] = f"IN_PROGRESS({len(done)}p/{st['total_count']}条)"
json.dump(st, open(STATE_F, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("pages:", len(done), "records:", len(st["records"]), "count:", st["total_count"], "status:", st["status"])
