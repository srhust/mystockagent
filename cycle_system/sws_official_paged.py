# -*- coding: utf-8 -*-
"""R38修正版官方渠道脚本(有界执行): 每轮≤3页/无重试/超时即停/断点续传/完整原始响应+全页SHA/candidate_keys命名.
状态: BLOCKED_TIMEOUT_SUSPECTED_RATE_LIMIT(超时未证实限流)."""
import json
import hashlib
import requests
import urllib3

urllib3.disable_warnings()
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/114.0.0.0 Safari/537.36"}
B = "https://www.swsresearch.com/institute-sw/api/"
STATE = r"E:\mystockagent\output\ashare\sws_r37d_resume.json"
PAGE_BUDGET = 3  # 每次运行最多3页(有界)

try:
    st = json.load(open(STATE, encoding="utf-8"))
except Exception:
    st = {"pages_done": [], "raw_pages": {}, "records": [], "status": "BLOCKED_TIMEOUT_SUSPECTED_RATE_LIMIT"}

new_pages, timeout_hit = {}, False
done = set(st["pages_done"])
for p in range(1, 43):
    if p in done or len(new_pages) >= PAGE_BUDGET:
        continue
    try:
        r = requests.get(B + "index_announcements/index/", params={"page": str(p), "page_size": "50"}, headers=H, timeout=30, verify=False)
        raw = r.text
        new_pages[str(p)] = {"sha256": hashlib.sha256(raw.encode()).hexdigest(), "raw": raw}  # 完整原始响应
        st["records"] += [{"id": x.get("id"), "declaredate": x.get("declaredate"), "title": x.get("title"), "page": p} for x in r.json()["data"]["results"]]
        done.add(p)
    except requests.exceptions.Timeout:
        timeout_hit = True  # 超时即停, 无重试
        break

st["pages_done"] = sorted(done)
st["raw_pages"].update(new_pages)
if len(done) >= 42:
    st["status"] = "COMPLETE_PAGED"
elif timeout_hit:
    st["status"] = "BLOCKED_TIMEOUT_SUSPECTED_RATE_LIMIT"
else:
    st["status"] = f"IN_PROGRESS({len(done)}/42)"
json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("pages:", len(done), "records:", len(st["records"]), "status:", st["status"], "timeout:", timeout_hit)
print("RESUME-OK")
