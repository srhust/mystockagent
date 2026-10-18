# -*- coding: utf-8 -*-
"""R37c: ①umi.js提取history端点调用参数 ②801038全量公告落盘(原始响应+SHA256) ③三行业过滤验证 ④标题类型分布 ⑤调整类公告附件探测."""
import re
import json
import hashlib
import requests
import urllib3

urllib3.disable_warnings()
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/114.0.0.0 Safari/537.36"}
B = "https://www.swsresearch.com/institute-sw/api/"

# 1) umi.js: history端点调用上下文(参数名)
umi = requests.get("https://www.swsresearch.com/institute_sw/umi.js", headers=H, timeout=30, verify=False).text
i = umi.find("index_publish/history")
ctx = umi[max(0, i - 300):i + 300]
params_hint = sorted(set(re.findall(r'(?:params|swindexcode|page|page_size|type|date)[":\s]+[\w"{]', ctx)))[:10]
print("history_ctx_params_hint:", params_hint)

def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]

# 2) history端点探测(多参数形态)
hist = {}
for pp in [{"swindexcode": "801038", "page": "1", "page_size": "10"}, {"swindexcode": "801038", "type": "component"}, {"indexcode": "801038"}]:
    try:
        r = requests.get(B + "index_publish/history/", params=pp, headers=H, timeout=20, verify=False)
        hist[json.dumps(pp, sort_keys=True)] = {"status": r.status_code, "head": r.text[:150], "sha": sha(r.text)}
    except Exception as ex:
        hist[json.dumps(pp, sort_keys=True)] = {"err": type(ex).__name__}
print("history_probe:", json.dumps({k: v.get("status", v.get("err")) for k, v in hist.items()}))

# 3) 三行业过滤验证(首页对比)
filt = {}
for code in ("801038", "801055", "801181"):
    r = requests.get(B + "index_announcements/index/", params={"swindexcode": code, "page": "1", "page_size": "50"}, headers=H, timeout=20, verify=False)
    d = r.json()["data"]
    filt[code] = {"count": d["count"], "first3": [(x["declaredate"][:10], x["title"][:24]) for x in d["results"][:3]], "sha": sha(r.text)}
print("filter_check:", json.dumps({k: (v["count"],) for k, v in filt.items()}), "distinct:", len({v["first3"][0][1] for v in filt.values()}) == 3)

# 4) 801038全量公告抓取落盘(42页)
allrecs = []
p = 1
while True:
    r = requests.get(B + "index_announcements/index/", params={"swindexcode": "801038", "page": str(p), "page_size": "50"}, headers=H, timeout=20, verify=False)
    d = r.json()["data"]["results"]
    if not d:
        break
    allrecs += d
    p += 1
    if p > 50:
        break
cats = Counter = {}
import collections
cnt = collections.Counter()
for x in allrecs:
    t = x["title"]
    c = "adjust" if ("调整" in t or "调入" in t or "更换" in t) else ("monthly" if "月末" in t else ("revision" if "分类" in t else "other"))
    cnt[c] += 1
    if c == "adjust":
        cats.setdefault("adjust_sample", []).append({"id": x["id"], "date": x["declaredate"][:10], "title": t[:50]})
print("n_total:", len(allrecs), "cats:", dict(cnt))
raw = {"fetched": "2026-10-18", "endpoint": B + "index_announcements/index/?swindexcode=801038", "n": len(allrecs), "sha256_pages_head": sha(json.dumps(allrecs[:50], ensure_ascii=False)), "records": allrecs, "filter_check": filt, "history_probe": hist, "umi_params_hint": params_hint, "cats": dict(cnt), "adjust_sample": cats.get("adjust_sample", [])[:10]}
json.dump(raw, open(r"E:\mystockagent\output\ashare\sws_801038_announcements_full.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("R37C-OK")
