# -*- coding: utf-8 -*-
"""阶段A工具: F2 复现 P0 独立重放验证(§1.2)。

从 git 历史恢复的精确参数集(config.F2_REPRO_PARAMS ← swing_flow_audit.py L10, R31 入库):
    run("expt", gate=True, exit_mode="monthly", entry_mode="daily", cap=0,
        alloc_policy="score", risk_mode="gate", gate_realloc="realloc")

已知磁盘旁证: 2026-10-18 11:57:07/11:57:11 一次重跑留下的 cap0 侧效文件
(output/ashare_v2/phaseA_evidence/cap0_20261018T1157_stats_extract.json) 与同时重写的
F2_realloc_full.json(被 git 判为与 HEAD 逐字节一致)。本脚本做**独立第三次重放**:
  ① 调用冻结的 swing_m1.run(不改其语义、不写冻结产物路径);
  ② 将重放结果与 F2 冻结账本逐字段比对;
  ③ 判定 F2_REPRO_RESOLVED(参数集完整可复现) 或 BASELINE_UNREPRODUCIBLE(显式标注)。

副作用声明: swing_m1.run() 自身会写 output/ashare/swing_m1_expt_True_monthly_3f_daily_cap0.json
(R19 实验ID防覆盖机制)。该文件非官方冻结产物(F2 官方产物为 *_F2_realloc_full.json),
重放后本仓库由维护者用 `git restore` 将其复位, 本报告同时记录重放时该文件的内容哈希。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
# 仓库根入 sys.path: 供包导入 + 旧模块的 `import config`(根config.py)。
# 注意: 绝不能把 ashare_v2 目录本身入 path——会遮蔽根 config(同名模块)。
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
from cycle_system.ashare_v2 import config as C  # noqa: E402

sys.path.insert(0, os.path.join(C.REPO_ROOT, "cycle_system"))
import swing_m1 as m  # noqa: E402  只读复用: 不修改其任何语义


def sha_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None


def summarize(r):
    st = r["stats"]
    nav = r["nav_log"]
    return {"return": st["return"], "ann": st["ann"], "max_dd": st["max_dd"], "calmar": st["calmar"],
            "sharpe": st["sharpe"], "buy": st["trades"]["BUY"], "sell": st["trades"]["SELL"],
            "s2gate": st["rejects"].get("s2gate", 0),
            "lot": st["rejects"].get("lot"), "execution_cash_reject": st["rejects"].get("execution_cash_reject"),
            "insufficient_cash": st["rejects"].get("insufficient_cash"),
            "n_nav": len(nav), "first_nav_day": nav[0]["date"], "last_nav_day": nav[-1]["date"],
            "last_nav": nav[-1]["nav"], "cash_pct": st.get("cash_pct"),
            "avg_hold_days": st.get("avg_hold_days"), "max_pos": st.get("max_pos")}


def main() -> int:
    f2_path = os.path.join(C.REPO_ROOT, "output", "ashare", "swing_m1_F2_realloc_full.json")
    sha_before = sha_file(f2_path)
    frozen = json.load(open(f2_path, encoding="utf-8"))
    fs = summarize(frozen)

    print(f"[f2_repro] 重放参数: {C.F2_REPRO_PARAMS}", flush=True)
    p = dict(C.F2_REPRO_PARAMS)
    strat = p.pop("strategy")
    r = m.run(strat, **p)
    rs = summarize(r)

    cap0 = os.path.join(C.REPO_ROOT, "output", "ashare", "swing_m1_expt_True_monthly_3f_daily_cap0.json")
    fields = ["return", "ann", "max_dd", "calmar", "sharpe", "buy", "sell", "s2gate",
              "lot", "execution_cash_reject", "insufficient_cash", "n_nav", "last_nav",
              "first_nav_day", "last_nav_day"]
    diff = {k: {"frozen": fs.get(k), "replay": rs.get(k)} for k in fields if fs.get(k) != rs.get(k)}
    resolved = not diff and sha_file(f2_path) == sha_before

    verdict = "F2_REPRO_RESOLVED" if resolved else "BASELINE_UNREPRODUCIBLE"
    tz = timezone(timedelta(hours=8))
    out = {
        "task": "F2复现P0处置(TASK_SPEC §1.2)",
        "verdict": verdict,
        "params_recovered_from": "git: cycle_system/swing_flow_audit.py L10 (R31入库, commit 616fc0d 之前已存在)",
        "params": {"strategy": strat, **p},
        "ledger_reference": C.F2_LEDGER,
        "frozen_artifact_sha256_before_after": [sha_before, sha_file(f2_path)],
        "side_effect_cap0_file_sha256_after_replay": sha_file(cap0),
        "summary_frozen": fs, "summary_replay": rs, "field_diff": diff,
        "corroborating_disk_evidence": "output/ashare_v2/phaseA_evidence/cap0_20261018T1157_stats_extract.json "
                                       "(2026-10-18 11:57 重跑, F2产物与HEAD逐字节一致但未留档参数)",
        "implication": ("阶段A(1)冻结基线未变+本重放一致 → '工程可复现'恢复成立, V2对照基线=此参数集重放产物; "
                        "f2_repro_p0.json 原件保留不改(历史证据), 其 OPEN_P0 状态由本文件解决。"
                        if resolved else
                        "V2对照基线标注 BASELINE_UNREPRODUCIBLE; 冻结数字仅作'历史快照值, 当前不可复现'引用。"),
        "_provenance": {"code_version": os.popen("git -C " + C.REPO_ROOT + " rev-parse HEAD").read().strip(),
                        "config_hash": C.config_hash(), "generated_at": datetime.now(tz).isoformat(),
                        "mode": "SYNTHETIC", "asof": datetime.now(tz).strftime("%Y-%m-%d"),
                        "pit_notes": ["工程可复现性验证, 非绩效声明; 2020-2026为研究窗口非OOS"]},
    }
    out_dir = os.path.join(C.OUTPUT_DIR, "f2_repro")
    os.makedirs(out_dir, exist_ok=True)
    json.dump(out, open(os.path.join(out_dir, "f2_repro_resolution.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"verdict": verdict, "field_diff_keys": list(diff)}, ensure_ascii=False))
    return 0 if resolved else 1


if __name__ == "__main__":
    sys.exit(main())
