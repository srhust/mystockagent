# -*- coding: utf-8 -*-
"""st_interval_szse — 深市官方简称变更表→ST/*ST/PT状态区间构造(A4指标4深市侧, 一次API).

源=SZSE官方(ak.stock_info_sz_change_name 简称变更, 全市场7495行/1994-)。规则:
变更后含ST/*ST/PT→状态起(=变更日期); 变更前含而变更后不含→止(=变更日期, 区间尾开)。
输出: security_master/st_intervals_szse.json + manifest sha256。与baostock isST(待通道)
=双源, 重合度进DATA_CONFLICT。纯本地一次调用。
"""
import hashlib, json, os, re, sys, threading
from datetime import datetime, date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                        # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                    # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000", "security_master")
PAT = re.compile(r"(^|[^A-Za-z])(\*?ST|PT|S\*?ST|SST)")


def is_st(name: str) -> bool:
    return bool(name) and bool(PAT.search(str(name).upper().replace(" ", "")))


def main() -> int:
    import akshare as ak
    box = {}

    def w():
        try:
            box["df"] = ak.stock_info_sz_change_name(symbol="简称变更")
        except Exception as e:
            box["exc"] = e
    t = threading.Thread(target=w, daemon=True)
    t.start()
    t.join(60)
    if "df" not in box:
        print("SOURCE FAIL:", box.get("exc", "HANG60"))
        return 7
    df = box["df"]
    raw_p = os.path.join(ROOT, "szse_name_change_raw.csv")
    tmp = raw_p + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, raw_p)
    sha = hashlib.sha256(open(raw_p, "rb").read()).hexdigest()
    intervals = {}
    for code, g in df.groupby("证券代码"):
        g = g.sort_values("变更日期")
        cur, start = False, None
        segs = []
        for _, r in g.iterrows():
            after = is_st(r.get("变更后简称"))
            if after and not cur:
                cur, start = True, str(r["变更日期"])
            elif not after and cur:
                cur = False
                segs.append({"start": start, "end": str(r["变更日期"]), "state": "ST"})
        if cur:
            segs.append({"start": start, "end": None, "state": "ST_OPEN"})
        if segs:
            intervals[str(code)] = segs
    out = {"source": "SZSE官网简称变更(经akshare)", "fetched": datetime.now().isoformat(timespec="seconds"),
           "raw_sha256": sha, "rows": len(df), "codes_with_st": len(intervals),
           "note": "ST起=变更后简称含ST/PT的变更日期; 止=下一变更日(开区间); ST_OPEN=采集时仍ST",
           "intervals": intervals}
    ip = os.path.join(ROOT, "st_intervals_szse.json")
    tmp2 = ip + ".tmp"
    json.dump(out, open(tmp2, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp2, ip)
    print("SZSE ST intervals:", len(intervals), "codes /", sum(len(v) for v in intervals.values()), "段; rows", len(df), "sha", sha[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main())
