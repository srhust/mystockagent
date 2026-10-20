# -*- coding: utf-8 -*-
"""st_interval_sh_sina — 沪市ST/PT更名史(sina公司简档逐券, 21:00后跑避C层窗口).

源=ak.stock_info_change_name(symbol)(sina vCI_CorpInfo页, 每券更名史时间线, 列index/name)。
缺陷: 该表**无日期列**!! 实测只有名字序列 → 单独不可建区间。
=> 本工具先实证记录(试点3券), 若确无日期则沪市侧改判: 唯一官方路径=SSE官网更名史页
   (未封装, 待直接端点勘探) + baostock isST(通道恢复)——本脚本输出=证据文件非区间。
"""
import json, os, sys, threading
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                        # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                    # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
OUT = os.path.join(ROOT, "QC", "st_sh_evidence.json")


def one(ak, num, bound=20):
    box = {}
    def w():
        try: box["df"] = ak.stock_info_change_name(symbol=num)
        except Exception as e: box["exc"] = f"{type(e).__name__}:{str(e)[:60]}"
    t = threading.Thread(target=w, daemon=True); t.start(); t.join(bound)
    return box


def main() -> int:
    import akshare as ak
    codes = [l.strip().split(".")[1] for l in
             open(os.path.join(ROOT, "universe", "2000", "alive.csv"), encoding="utf-8")
             if l.strip().startswith("sh.")]
    res = {"n_sh_alive_2000": len(codes), "sample": {}, "has_date_col": None}
    for num in codes[:3] + ["600001", "600002", "600068", "600070"]:   # 含死券
        b = one(ak, num)
        if "df" in b:
            df = b["df"]
            cols = list(df.columns)
            if not cols or df.empty:
                res["sample"][num] = {"cols": cols, "rows": 0, "names": [], "raw": str(df.head(2))[:120]}
                continue
            namecol = "name" if "name" in cols else (cols[1] if len(cols) > 1 else cols[0])
            res["sample"][num] = {"cols": cols, "rows": len(df),
                                  "names": [str(x)[:20] for x in df[namecol].tolist()][-8:]}
            res["has_date_col"] = any("date" in c.lower() or "日期" in c for c in df.columns)
        else:
            res["sample"][num] = {"fail": b.get("exc", "HANG20")}
        import time; time.sleep(1.0)
    tmp = OUT + ".tmp"
    json.dump(res, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print(json.dumps(res, ensure_ascii=False, indent=1)[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
