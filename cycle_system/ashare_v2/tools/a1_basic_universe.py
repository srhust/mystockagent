# -*- coding: utf-8 -*-
"""A1 历史证券全集落档: query_stock_basic 全量快照 + A2 边界语义交叉验证."""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                    # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                # noqa: E402
import baostock as bs                                                          # noqa: E402

OUT = os.path.join(C.DATA_DIR, "ashare_history_2000", "security_master")


def main() -> int:
    lg = bs.login()
    assert lg.error_code == "0", lg.error_msg
    rs = bs.query_stock_basic()
    assert rs.error_code == "0", rs.error_msg
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    bs.logout()
    dest = os.path.join(OUT, "basic_full_snapshot_2026-10-20.csv")
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(rs.fields) + "\n")
        for r in rows:
            f.write(",".join(x.replace(",", ";") for x in r) + "\n")
    os.replace(tmp, dest)                                    # 裁定A2.4 原子替换首用
    sha = hashlib.sha256(open(dest, "rb").read()).hexdigest()
    # A2 边界交叉验证
    ext = json.load(open(os.path.join(C.DATA_DIR, "ashare_history_2000", "QC",
                                      "delist_boundary_extended.json"), encoding="utf-8"))
    probe = json.load(open(os.path.join(C.DATA_DIR, "ashare_history_2000", "QC",
                                        "delist_boundary_probe.json"), encoding="utf-8"))
    info = {r[0]: r for r in rows}
    cross = []
    for code, meta in {**{r["code"]: {"last": r["last_bar_day"]} for r in probe["records"]},
                       **{k: {"last": v["true_last_bar"]} for k, v in ext.items() if k.startswith("sh")}}.items():
        r = info.get(code)
        cross.append({"code": code, "bar_last": meta["last"], "outDate": r[3] if r else "N/A",
                      "status": r[5] if r else "N/A", "name": r[1] if r else "",
                      "match": bool(r and (r[3] or "") == (meta["last"] or ""))})
    man = {"snapshot": "query_stock_basic全量", "rows": len(rows),
           "status0_delisted": sum(1 for r in rows if len(r) > 5 and r[5] == "0"),
           "type1_stocks": sum(1 for r in rows if len(r) > 4 and r[4] == "1"),
           "sha256": sha, "atomic_write": "tmp+os.replace(裁定A2.4首用)",
           "semantics_crosscheck_outDate_vs_bar_last": cross,
           "note": "A1历史universe官方源=ipoDate/outDate/status; outDate语义以交叉验证定; 空outDate=仍在市"}
    json.dump(man, open(os.path.join(OUT, "basic_full_manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps({k: man[k] for k in ("rows", "status0_delisted", "type1_stocks")}, ensure_ascii=False))
    for c in cross:
        print(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
