# -*- coding: utf-8 -*-
"""R21perf: 面板级缓存(249CSV→parquet一次聚合, 数据变更自动失效) + 批量并行消融 + cProfile。

数据版本判定: data/stocks/*.csv 最大mtime > parquet mtime → 重建。
纯缓存层, 不改变任何计算口径(load_panel逻辑原样)。
"""
import os
import glob as _glob
import time
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

sys_p = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, sys_p)
import swing_m1 as _m1
_ORIG_LOAD = _m1.load_panel  # 原函数引用(防注入递归)

CACHE = r"E:\mystockagent\output\ashare\_panel_cache.parquet"


def _data_stamp() -> float:
    ts = [os.path.getmtime(f) for f in _glob.glob(r"E:\mystockagent\data\stocks\*.csv")]
    return max(ts) if ts else 0.0


def load_panel_cached():
    """面板缓存读取(口径同swing_m1.load_panel, 仅IO层)。"""
    fresh = _data_stamp()
    if os.path.exists(CACHE) and os.path.getmtime(CACHE) >= fresh:
        return pd.read_parquet(CACHE)
    panel = _ORIG_LOAD()
    try:
        panel.to_parquet(CACHE)
    except Exception as _e:  # pyarrow缺失等: 缓存失败不阻断
        print(f"[panel-cache] skip: {_e}")
    return panel


def _run_one(args):
    tag, kwargs = args
    import swing_m1 as m
    m.load_panel = load_panel_cached  # 注入缓存版IO
    t0 = time.time()
    r = m.run(**kwargs)
    return tag, r, round(time.time() - t0, 1)


def run_batch(experiments, workers: int = 4):
    """批量并行消融: experiments=[(tag, {run_kwargs}), ...], 进程隔离(FACTOR_TABLE各自构建)。"""
    out = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for tag, r, secs in ex.map(_run_one, experiments):
            s = r["stats"]
            out[tag] = {k: s.get(k) for k in ("return", "max_dd", "calmar", "sharpe", "cash_pct", "avg_pos", "max_pos", "flat_days", "avg_hold_days")}
            out[tag]["_secs"] = secs
            print(f"[batch] {tag} done in {secs}s", flush=True)
    return out


def profile_one(kwargs: dict, top: int = 25):
    """单组cProfile: 热点精确到函数。"""
    import cProfile, pstats, io as _io
    import swing_m1 as m
    m.load_panel = load_panel_cached
    pr = cProfile.Profile()
    pr.enable()
    m.run(**kwargs)
    pr.disable()
    s = _io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(top)
    txt = s.getvalue()
    with open(r"E:\mystockagent\output\ashare\_profile.txt", "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt[:3000])


if __name__ == "__main__":
    import json
    A = run_batch([
        ("A0", dict(strategy="expt", gate=True, exit_mode="monthly", entry_mode="daily", cap=0, alloc_policy="equal")),
        ("A1", dict(strategy="expt", gate=True, exit_mode="monthly", entry_mode="daily", cap=0, alloc_policy="score")),
        ("A2", dict(strategy="expt", gate=True, exit_mode="monthly", entry_mode="daily", cap=8, alloc_policy="score")),
        ("A3", dict(strategy="expt", gate=True, exit_mode="monthly", entry_mode="daily", cap=5, alloc_policy="free_slots")),
    ])
    A["_wall"] = "parallel"
    json.dump({"caveats": {"rank_caliber": "v2预计算滚动20日", "engine": "panel-cache+parallel"}, "A": A},
              open(r"E:\mystockagent\output\ashare\swing_alloc_ablation.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(A, ensure_ascii=False))
