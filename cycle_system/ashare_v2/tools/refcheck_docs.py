# -*- coding: utf-8 -*-
"""引用存在性批验: 索引与评审包中的反引号路径全部核对。"""
import glob as g
import os
import re
import sys

sys.path.insert(0, ".")
BASE = "output/ashare_v2"
docs = [f"{BASE}/independent/PHASE4_ACCEPTANCE_INDEX.md",
        f"{BASE}/independent/M3_REVIEW_PACKAGE.md"]
bad = []
n = 0
for doc in docs:
    txt = open(doc, encoding="utf-8").read()
    for ref in set(re.findall(r"`([^`]+?\.(?:json|csv|md|py))`", txt)):
        n += 1
        cands = [ref, os.path.join(BASE, ref),
                 os.path.join("data/ashare_history_2000", ref.replace("QC/", "QC/")),
                 os.path.join("cycle_system/ashare_v2", ref),
                 os.path.join("data", ref), ref.replace("independent/", f"{BASE}/independent/")]
        if any(os.path.exists(c) for c in cands) or g.glob(ref):
            continue
        # 目录引用
        if any(os.path.isdir(c) for c in cands):
            continue
        bad.append((os.path.basename(doc), ref))
print(f"checked {n} refs, broken {len(bad)}")
for b in bad:
    print("BROKEN:", b)
