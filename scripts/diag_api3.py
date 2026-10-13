# -*- coding: utf-8 -*-
"""时序对比：确认东财接口失败是否为间歇性。"""
import os
import socket
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

import requests  # noqa: E402

os.environ["NO_PROXY"] = "*"

PARAMS = {
    "pn": "1", "pz": "5", "po": "1", "np": "1",
    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    "fltt": "2", "invt": "2", "fid": "f3",
    "fs": "m:90 t:2 f:!50",
    "fields": "f12,f14",
}
QS = "&".join(f"{k}={v}" for k, v in PARAMS.items())

print("== DNS ==")
for host in ["17.push2.eastmoney.com", "push2.eastmoney.com", "push2his.eastmoney.com"]:
    try:
        ips = socket.getaddrinfo(host, 443, socket.AF_INET)
        print(f"  {host} -> {ips[0][4][0]}")
    except Exception as e:
        print(f"  {host} -> DNS FAIL {e}")

for rnd in range(3):
    print(f"\n===== 第 {rnd + 1} 轮 =====")
    # 1. urllib 直连
    try:
        req = urllib.request.Request("http://17.push2.eastmoney.com/api/qt/clist/get?" + QS)
        r = urllib.request.urlopen(req, timeout=8)
        print(f"[urllib http] OK {r.status}")
    except Exception as e:
        print(f"[urllib http] FAIL {type(e).__name__} {str(e)[:80]}")
    # 2. requests trust_env=False
    try:
        s = requests.Session()
        s.trust_env = False
        r = s.get("http://17.push2.eastmoney.com/api/qt/clist/get", params=PARAMS, timeout=8)
        print(f"[requests direct http] OK {r.status_code}")
    except Exception as e:
        print(f"[requests direct http] FAIL {type(e).__name__} {str(e)[:80]}")
    # 3. requests + NO_PROXY
    try:
        r = requests.get("http://17.push2.eastmoney.com/api/qt/clist/get", params=PARAMS, timeout=8)
        print(f"[requests NO_PROXY http] OK {r.status_code}")
    except Exception as e:
        print(f"[requests NO_PROXY http] FAIL {type(e).__name__} {str(e)[:80]}")
    # 4. akshare stock_zh_a_hist
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol="600000", period="daily",
                                start_date="20250101", end_date="20250630", adjust="qfq")
        print(f"[akshare stock_zh_a_hist] OK {df.shape}")
    except Exception as e:
        print(f"[akshare stock_zh_a_hist] FAIL {type(e).__name__} {str(e)[:80]}")
    time.sleep(3)
