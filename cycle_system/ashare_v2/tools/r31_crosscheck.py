# -*- coding: utf-8 -*-
"""r31_crosscheck — 批4(2020-2026) sina 抓取 vs R31 冻结面板逐券逐日对账(A3裁定项).

输入: R31=output/ashare/_panel_cache.parquet(date,code,sector,open_raw,close,close_adj,amount; close=raw)
      M4 =data/ashare_history_2000/daily_sina/{Y}/{code}.csv(sina raw+增益列, 同基已证)
规则: 按 (code,date) join, |sina.close - r31.close| / r31.close > tol(默认0.5%) → 冲突登记;
      **只登记不覆盖**(红线); 输出 QC/r31_crosscheck_{Y}.json(容差分桶+样例+覆盖率)。
用法: python r31_crosscheck.py --years 2020-2026 [--tol 0.005] [--synthetic-test]
"""
import argparse, json, os, sys
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                        # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                                    # noqa: E402

ROOT = os.path.join(C.DATA_DIR, "ashare_history_2000")
PANEL = os.path.join(os.path.abspath(os.path.join(os.path.dirname(_path.__file__), "..", "..")),
                     "output", "ashare", "_panel_cache.parquet")


def to_bst(code: str) -> str:
    return ("sh." if code.startswith(("6",)) else "sz.") + code


def crosscheck_year(r31: pd.DataFrame, y: int, tol: float):
    d = r31[r31["date"].dt.year == y]
    conflicts, checked, missing_files = [], 0, []
    diffs = []
    for code, g in d.groupby("code"):
        p = os.path.join(ROOT, "daily_sina", str(y), f"{to_bst(code)}.csv")
        if not os.path.exists(p):
            missing_files.append(code)
            continue
        sina = pd.read_csv(p)
        sina["date"] = sina["date"].astype(str).str[:10]
        m = g.assign(date=g["date"].dt.strftime("%Y-%m-%d"))[["date", "close"]].merge(
            sina[["date", "close"]], on="date", suffixes=("_r31", "_sina"))
        checked += len(m)
        if not len(m):
            continue
        rel = (m["close_sina"] - m["close_r31"]).abs() / m["close_r31"].abs().clip(lower=1e-9)
        diffs.extend(rel.tolist())
        bad = m[rel > tol]
        for _, r in bad.head(5).iterrows():
            conflicts.append({"code": code, "date": r["date"], "r31": float(r["close_r31"]),
                              "sina": float(r["close_sina"]), "rel": round(float(rel[r.index]), 5)})
    rel_ok = None
    if diffs:
        s = pd.Series(diffs)
        rel_ok = {"n_pairs": len(s), "median_rel": round(float(s.median()), 6),
                  "p99_rel": round(float(s.quantile(0.99)), 6),
                  "within_tol_share": round(float((s <= tol).mean()), 4)}
    rep = {"year": y, "r31_codes": int(d["code"].nunique()), "files_missing": len(missing_files),
           "pairs_checked": checked, "tol": tol, "agree": rel_ok,
           "conflict_sample": conflicts[:20], "n_conflicts_over_tol": int(sum(
               1 for x in (diffs or []) if x > tol)),
           "rule": "只登记不覆盖; 冲突入DATA_CONFLICT三源仲裁(批4前置)"}
    out = os.path.join(ROOT, "QC", f"r31_crosscheck_{y}.json")
    tmp = out + ".tmp"
    json.dump(rep, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, out)
    print(y, "checked", checked, "missing_files", len(missing_files),
          "over_tol", rep["n_conflicts_over_tol"], "agree", rel_ok)
    return rep


def synthetic_test():
    """从R31面板造2020合成sina csv→零差异自证管道(测完删, 不污染数据层)。"""
    r31 = pd.read_parquet(PANEL)
    d = r31[r31["date"].dt.year == 2020]
    made = []
    for code, g in list(d.groupby("code"))[:3]:
        bst = to_bst(code)
        out = os.path.join(ROOT, "daily_sina", "SYNTHTEST", f"{bst}.csv")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        pd.DataFrame({"date": g["date"].dt.strftime("%Y-%m-%d"), "close": g["close"]}).to_csv(out, index=False)
        made.append(out)
    # 手工比对(用与正式一致的逻辑路径)
    ok = 0
    for p, (code, g) in zip(made, list(d.groupby("code"))[:3]):
        sina = pd.read_csv(p)
        m = g.assign(date=g["date"].dt.strftime("%Y-%m-%d"))[["date", "close"]].merge(sina, on="date", suffixes=("_r", "_s"))
        rel = ((m["close_s"] - m["close_r"]).abs() / m["close_r"].abs().clip(lower=1e-9)).max()
        ok += int(rel < 1e-9)
        print("synthetic", code, "max_rel", rel)
    for p in made:
        os.remove(p)
    os.rmdir(os.path.join(ROOT, "daily_sina", "SYNTHTEST"))
    print("SYNTHETIC", "PASS" if ok == 3 else f"FAIL {ok}/3")
    return 0 if ok == 3 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2020-2026")
    ap.add_argument("--tol", type=float, default=0.005)
    ap.add_argument("--synthetic-test", action="store_true")
    a = ap.parse_args()
    if a.synthetic_test:
        return synthetic_test()
    r31 = pd.read_parquet(PANEL)
    lo, hi = (int(x) for x in a.years.split("-"))
    for y in range(lo, hi + 1):
        crosscheck_year(r31, y, a.tol)
    return 0


if __name__ == "__main__":
    sys.exit(main())
