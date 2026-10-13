# -*- coding: utf-8 -*-
"""迭代补齐 akshare 缺失依赖：import 失败 -> 从阿里云镜像下载对应 wheel -> 解压到 .libs。"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, ".libs"))

from pip_manual import download_pkg, unzip_to  # noqa: E402

MIRROR = "https://mirrors.aliyun.com/pypi/simple/"


def fetch_wheel_links(pkg):
    from pip_manual import fetch_url
    html = fetch_url(MIRROR + pkg + "/").decode("utf-8", "ignore")
    return re.findall(r'href="([^"]+)"[^>]*>([^<]+\.whl)<', html)


def pkg_name_candidates(mod):
    """导入名 -> PyPI 发行名候选（下划线/连字符变体）。"""
    return [mod, mod.replace("_", "-"), mod.replace("-", "_")]


def pick_wheel(names):
    """按兼容性优先级选 wheel: cp39-win_amd64 > cp39-abi3-win_amd64 > py3-none-win_amd64 > py3-none-any。"""
    def score(n):
        s = -1
        if "cp39" in n and "win_amd64" in n and "abi3" not in n:
            s = 100
        elif "cp39" in n and "abi3" in n and "win_amd64" in n:
            s = 90
        elif "win_amd64" in n and "py3-none" in n:
            s = 80
        elif "py3-none-any" in n:
            s = 50
        elif "any" in n and ("cp39" in n or "py3" in n):
            s = 30
        return s
    best = max(names, key=score)
    return best, score(best)


def install_pkg(mod):
    links = []
    for cand in pkg_name_candidates(mod):
        try:
            links = fetch_wheel_links(cand)
            if links:
                pkg = cand
                break
        except Exception:
            continue
    if not links:
        print(f"!! {mod}: 镜像上没有 wheel")
        return False
    # 版本排序：取文件名列表里可兼容的最高版本
    compat = [(h, n) for h, n in links if score_of(n) > 0]
    if not compat:
        print(f"!! {mod}: 没有兼容 cp39/win 的 wheel")
        return False
    href, name = max(compat, key=lambda x: (version_key(x[1]), score_of(x[1])))
    from urllib.parse import urljoin
    href = href.split("#")[0]
    href = urljoin(MIRROR + pkg + "/", href)
    dst = os.path.join(ROOT, ".pip", "dl", name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not (os.path.exists(dst) and os.path.getsize(dst) > 1000):
        print("下载:", name)
        from pip_manual import fetch_url
        data = fetch_url(href)
        with open(dst, "wb") as f:
            f.write(data)
    unzip_to(dst, os.path.join(ROOT, ".libs"))
    return True


def score_of(n):
    s = -1
    if "cp39" in n and "win_amd64" in n and "abi3" not in n:
        s = 100
    elif "cp39" in n and "abi3" in n and "win_amd64" in n:
        s = 90
    elif "win_amd64" in n and "py3-none" in n:
        s = 80
    elif "py3-none-any" in n:
        s = 50
    elif ("cp39" in n or "py3" in n) and "linux" not in n and "macos" not in n and "manylinux" not in n:
        s = 30
    return s


def version_key(n):
    m = re.search(r"-(\d+(?:\.\d+)*)", n)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.group(1).split("."))


SKIP = set()  # 跳过自动安装的模块（可选依赖）
MAX_ROUNDS = 40

ENV = dict(os.environ)
_libs = os.path.join(ROOT, ".libs")
ENV["PYTHONPATH"] = _libs + os.pathsep + ENV.get("PYTHONPATH", "")

for rnd in range(MAX_ROUNDS):
    r = subprocess.run([sys.executable, "-c", "import akshare"], capture_output=True, text=True, env=ENV)
    if r.returncode == 0:
        print(f"== akshare import 成功 (第{rnd + 1}轮) ==")
        break
    err = r.stderr.strip().splitlines()
    mod = None
    for line in err:
        m = re.search(r"ModuleNotFoundError: No module named '([\w\.]+)'", line)
        if m:
            mod = m.group(1).split(".")[0]
            break
    if mod is None:
        print("!! 无法解析的导入错误:")
        print("\n".join(err[-5:]))
        break
    if mod in SKIP:
        print(f"!! {mod} 已标记跳过但仍然缺失，停止")
        break
    print(f"-- 缺失: {mod}，尝试安装 --")
    if not install_pkg(mod):
        SKIP.add(mod)
        print(f"   {mod} 安装失败，将跳过（后续如必须需手动处理）")

# 最终验证
r = subprocess.run([sys.executable, "-c", "import akshare as ak; print('VERSION', ak.__version__)"],
                   capture_output=True, text=True, env=ENV)
print(r.stdout.strip() or r.stderr.strip()[-300:])
