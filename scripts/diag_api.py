# -*- coding: utf-8 -*-
"""诊断：1) eastmoney 直连 vs 代理；2) 找 PB 估值接口的正确函数名。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import requests  # noqa: E402
import urllib.request  # noqa: E402
import akshare as ak  # noqa: E402

URL = "http://17.push2.eastmoney.com/api/qt/clist/get"
PARAMS = {
    "pn": "1", "pz": "5", "po": "1", "np": "1",
    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    "fltt": "2", "invt": "2", "fid": "f3",
    "fs": "m:90+t:2",
    "fields": "f1,f2,f3,f12,f13,f14,f152,f3,f62",
}

print("== 1. requests 默认(走系统代理) ==")
try:
    r = requests.get(URL, params=PARAMS, timeout=10)
    print("OK", r.status_code, r.text[:80])
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:120])

print("== 2. requests trust_env=False (直连) ==")
try:
    s = requests.Session()
    s.trust_env = False
    r = s.get(URL, params=PARAMS, timeout=10)
    print("OK", r.status_code, r.text[:80])
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:120])

print("== 3. urllib 直连(no_proxy=*) ==")
try:
    os.environ["NO_PROXY"] = "*"
    req = urllib.request.Request(URL + "?" + "&".join(f"{k}={v}" for k, v in PARAMS.items()))
    r = urllib.request.urlopen(req, timeout=10)
    print("OK", r.status, r.read()[:80])
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:120])

print("== 4. akshare 里可用的估值/指标函数 ==")
cands = [x for x in dir(ak) if any(k in x.lower() for k in ("indicator", "valuat", "_lg", "pb"))]
for c in sorted(cands):
    print("  ", c)

print("== 5. 系统代理探测 ==")
print("getproxies:", urllib.request.getproxies())
