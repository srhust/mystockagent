# -*- coding: utf-8 -*-
"""通过 urllib 直接从阿里云镜像下载 akshare wheel 并解压到 .libs。"""
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DL = os.path.join(ROOT, ".pip", "dl")
LIBS = os.path.join(ROOT, ".libs")
os.makedirs(DL, exist_ok=True)
os.makedirs(LIBS, exist_ok=True)

MIRROR = "https://mirrors.aliyun.com/pypi/simple/"


def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=60).read()


def download_pkg(pkg):
    """下载指定包的最新 py3-none-any wheel，返回本地路径。"""
    html = fetch_url(MIRROR + pkg + "/").decode("utf-8", "ignore")
    links = re.findall(r'href="([^"]+)"[^>]*>([^<]+\.whl)<', html)
    # 优先 py3-none-any 纯 python wheel
    pure = [(h, n) for h, n in links if "py3-none-any" in n]
    if not pure:
        raise RuntimeError(f"{pkg}: 未找到纯 python wheel, 全部: {[n for _, n in links[-5:]]}")
    href, name = pure[-1]
    href = href.split("#")[0]
    from urllib.parse import urljoin
    href = urljoin(MIRROR + pkg + "/", href)
    dst = os.path.join(DL, name)
    if not (os.path.exists(dst) and os.path.getsize(dst) > 10000):
        print("下载:", name)
        data = fetch_url(href)
        with open(dst, "wb") as f:
            f.write(data)
    print("  ->", os.path.getsize(dst), "bytes")
    return dst


def unzip_to(wheel_path, dest):
    import zipfile
    with zipfile.ZipFile(wheel_path) as z:
        z.extractall(dest)
    print("解压:", os.path.basename(wheel_path), "->", dest)


if __name__ == "__main__":
    pkgs = sys.argv[1:] or ["akshare"]
    for pkg in pkgs:
        w = download_pkg(pkg)
        unzip_to(w, LIBS)
    print("DONE")
