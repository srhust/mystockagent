# -*- coding: utf-8 -*-
"""R37d(免费渠道最终验证): A全量2054公告落盘+分类; B umi.js真实调用参数追踪. 止损后不再探索."""
import re
import json
import hashlib
import collections
import requests
import urllib3

urllib3.disable_warnings()
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/114.0.0.0 Safari/537.36"}
B = "https://www.swsresearch.com/institute-sw/api/"

# A) 全量公告(42页, 完整落盘)
allrecs, page_shas = [], []
p = 1
while p <= 50:
    r = requests.get(B + "index_announcements/index/", params={"page": str(p), "page_size": "50"}, headers=H, timeout=40, verify=False)
    d = r.json()["data"]["results"]
    page_shas.append(hashlib.sha256(r.text.encode()).hexdigest())
    if not d:
        break
    allrecs += [{k: x.get(k) for k in ("id", "declaredate", "title")} | {"page": p} for x in d]
    p += 1
cnt = collections.Counter()
adj = []
for x in allrecs:
    t = x["title"] or ""
    c = "ind_adjust" if ("行业" in t and ("调整" in t or "更换" in t or "纳入" in t)) else ("sw_revision" if "分类" in t and ("修订" in t or "变更" in t or "新版" in t) else ("stock_idx" if "指数成份股" in t or "指数成分股" in t else ("monthly" if "月末" in t else "other")))
    cnt[c] += 1
    if c in ("ind_adjust", "sw_revision"):
        adj.append({"id": x["id"], "date": str(x["declaredate"])[:10], "title": t[:60]})
out = {"fetched": "2026-10-18", "endpoint": B + "index_announcements/index/", "n": len(allrecs), "page_shas_sha_head": page_shas[:3], "full_records": allrecs, "cats": dict(cnt), "adjust_like_sample": adj[:15], "note": "完整原始记录落盘(可独立复核); 分类按标题关键词, '调整'不等于行业归属变更证据"}
json.dump(out, open(r"E:\mystockagent\output\ashare\sws_announcements_catalog.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("n:", len(allrecs), "cats:", dict(cnt))

# B) umi.js 真实调用参数(两端点调用位置±400字符, 提取属性键)
umi = requests.get("https://www.swsresearch.com/institute_sw/umi.js", headers=H, timeout=30, verify=False).text
ctxs = {}
for ep in ("index_announcements/index", "index_publish/history"):
    pos = [m.start() for m in re.finditer(re.escape(ep), umi)]
    keys = set()
    for i in pos:
        seg = umi[max(0, i - 500):i + 500]
        keys |= set(re.findall(r'(\w{3,30})\s*[:=]', seg))
    ctxs[ep] = {"call_sites": len(pos), "plausible_param_keys": sorted(keys - {"function", "return", "var", "case", "break", "catch", "Object", "Array"})[:25]}
print(json.dumps(ctxs, ensure_ascii=False)[:500])
json.dump(ctxs, open(r"E:\mystockagent\output\ashare\sws_umi_param_trace.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("R37D-OK")
