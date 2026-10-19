# -*- coding: utf-8 -*-
"""corrections_log — 包E: 更正记录追加机制(裁定E: "更正记录追加不改写, 关联原hash")。

原则: 结论更正**不得改写原文**, 而是在 append-only 账本登记:
  {id, ts, original_sha256, original_ref(文件/章节定位), original_excerpt(截断引用),
   corrected_to, ruling_ref, scope}
原文件如被后续版本正常演进(新commit), hash锚定的是**被更正当时**的文本状态。
账本自身只追加; 重复登记同一(original_sha256, corrected_to) → 幂等拒绝。

用法:
  python -X utf8 tools/corrections_log.py register --ref <定位> --excerpt "<原文>" \
      --corrected-to "<更正后表述>" --ruling "<裁定条款>"
  python -X utf8 tools/corrections_log.py list
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                  # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                              # noqa: E402

LOG_P = os.path.join(C.OUTPUT_DIR, "independent", "CORRECTIONS.jsonl")


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def register(ref: str, excerpt: str, corrected_to: str, ruling: str,
             ts: str | None = None) -> dict:
    """追加一条更正; 返回新记录。同(original_sha, corrected_to)已存在 → 拒绝(幂等)。"""
    h = sha256_text(excerpt)
    existing = []
    if os.path.exists(LOG_P):
        with open(LOG_P, encoding="utf-8") as f:
            existing = [json.loads(l) for l in f if l.strip()]
    if any(e["original_sha256"] == h and e["corrected_to"] == corrected_to for e in existing):
        raise RuntimeError(f"DUPLICATE_CORRECTION(幂等拒绝): {ref}")
    rec = {"id": len(existing) + 1, "ts": ts or datetime.now().astimezone().isoformat(timespec="seconds"),
           "original_ref": ref, "original_sha256": h,
           "original_excerpt": excerpt[:200], "corrected_to": corrected_to,
           "ruling_ref": ruling, "op": "APPEND_ONLY"}
    with open(LOG_P, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register")
    r.add_argument("--ref", required=True)
    r.add_argument("--excerpt", required=True)
    r.add_argument("--corrected-to", required=True, dest="corrected_to")
    r.add_argument("--ruling", required=True)
    r.add_argument("--ts", default=None)
    sub.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "register":
        rec = register(a.ref, a.excerpt, a.corrected_to, a.ruling, a.ts)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    for line in open(LOG_P, encoding="utf-8"):
        r_ = json.loads(line)
        print(f"#{r_['id']} [{r_['ts']}] {r_['original_ref']} sha={r_['original_sha256'][:12]} "
              f"→ {r_['corrected_to'][:60]} ({r_['ruling_ref'][:40]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
