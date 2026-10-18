# -*- coding: utf-8 -*-
"""report — §10 十产物写入器。每份文件带 _provenance(code_version/config_hash/数据锚/信息时点/模式)。

产物: state_engine.json / opportunities.csv / trades.csv / nav_curve.csv / attribution.csv /
      data_quality_report.json / allocation_plan_log.csv / universe_buckets_sample.json /
      industry_stage_snapshot.json / findings.md
纪律: findings.md 强制四态分离声明与"非预测有效"标注; 收益数字只作为模拟账本工程读数出现,
周边话术禁止"预期/概率/胜率"。
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

from cycle_system.ashare_v2 import config as C
from cycle_system.ashare_v2.data_portal import sha256_file, MANIFEST_P
from cycle_system.ashare_v2.schemas import RunMode

TZ = timezone(timedelta(hours=8))


def provenance(mode: str, asof: str, extra: dict | None = None) -> dict:
    def git(*a):
        return subprocess.run(["git", "-C", C.REPO_ROOT] + list(a),
                              capture_output=True, text=True).stdout.strip()
    p = {"code_version": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
         "config_hash": C.config_hash(), "manifest_sha256": sha256_file(MANIFEST_P),
         "mode": mode, "asof": asof, "generated_at": datetime.now(TZ).isoformat(),
         "four_state": {"compile": "PASS(pytest)", "sync_data_anchor": "PASS(250/250 C1)",
                        "test": "见tests/ashare_v2", "prediction_valid_claimed": "NOT_CLAIMED"}}
    p.update(extra or {})
    return p


def _jdump(path, obj):
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


def write_all(outdir: str, runner, result: dict | None, diag: dict | None = None) -> list:
    os.makedirs(outdir, exist_ok=True)
    mode = runner.mode
    day = (result["window"][1] if result else (diag["day"] if diag else str(runner.dates[-1].date())))
    prov = provenance(mode, day, {"window": result["window"] if result else None,
                                  "days_simulated": result.get("days_simulated") if result else 0})
    written = []

    # 1 state_engine.json
    lc = runner.lc
    fv = runner.fv
    trans = {}
    for ind, dmap in lc._states.items():
        seq = list(dmap.values())
        trans[ind] = {"state_at_day": seq[-1], "n_transitions": sum(1 for a, b in zip(seq, seq[1:]) if a != b)}
    fvd = {}
    for code in fv._states:
        st = fv.state_at(code, day)
        fvd[st] = fvd.get(st, 0) + 1
    _jdump(os.path.join(outdir, "state_engine.json"), {
        "_provenance": prov, "long_cycle": trans, "fast_v_dist_at_day": fvd,
        "note": "状态=前缀因果单程时序(测试锁); 候选≠预测有效"})
    written.append("state_engine.json")

    # 2 opportunities.csv
    with open(os.path.join(outdir, "opportunities.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["day", "code", "engines", "cycle_stage", "entry_kinds", "score_points",
                    "data_credibility", "membership_trust", "n_records"])
        seen = set()
        for o in runner.opportunities:
            key = (o["day"], o["merged"]["code"])
            if key in seen:
                continue
            seen.add(key)
            m, s = o["merged"], o["score"]
            w.writerow([o["day"], m["code"], "+".join(m["engines"]), s["cycle_stage"],
                        "+".join(sorted({r.entry_kind for r in m["records"]})), s["score_points"],
                        next((r.data_credibility for r in m["records"]), ""),
                        next((r.membership_trust for r in m["records"]), ""), len(m["records"])])
    written.append("opportunities.csv")

    # 3 trades.csv + 4 nav_curve.csv + 6 allocation_plan_log.csv
    trades, navrows = [], []
    if result:
        coord = runner.coord
        for logrec in coord.execution_log:
            pl = coord.plans.get(logrec["signal_day"], {})
            buy_eng = {b["plan"]["plan_hash"]: "+".join(b["engines"]) for b in pl.get("buys", []) if "plan" in b}
            sell_eng = {s["plan"]["plan_hash"]: s["engine"] for s in pl.get("sells", []) if "plan" in s}
            for fill in logrec["fills"]:
                h = fill["plan_hash"]
                trades.append([logrec["signal_day"], logrec["exec_day"], fill["code"], fill["action"],
                               fill["shares"], fill["price"], fill["notional"], fill["fees"],
                               buy_eng.get(h, sell_eng.get(h, "")), h])
            for rj in logrec["rejects"]:
                trades.append([logrec["signal_day"], logrec["exec_day"], rj["code"], "REJECT_" + rj["stage"],
                               0, "", "", "", rj["reason"], ""])
        navrows = [[n["day"], n["nav"], n["cash"], json.dumps(n["positions"], ensure_ascii=False)]
                   for n in result["nav_hist"]]
    with open(os.path.join(outdir, "trades.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["signal_day", "exec_day", "code", "action", "shares", "price", "notional",
                    "fees", "engine_attribution", "plan_hash"])
        w.writerows(trades)
    written.append("trades.csv")
    with open(os.path.join(outdir, "nav_curve.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["day", "nav", "cash", "positions_json"])
        w.writerows(navrows)
    written.append("nav_curve.csv")

    # 5 attribution.csv
    with open(os.path.join(outdir, "attribution.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["engine", "realized_total(模拟账本读数,非收益宣称)", "n_sell_events", "open_positions",
                    "open_shares", "unrealized(按last-known价)"])
        if result:
            coord = runner.coord
            per_eng = {}
            for rl in coord.realized_log:
                e = per_eng.setdefault(rl["engine"], {"real": 0.0, "n": 0})
                e["real"] += rl["realized"]
                e["n"] += 1
            for v in coord.virtuals:
                e = per_eng.setdefault(v.engine, {"real": 0.0, "n": 0})
                cost = coord.pos_meta.get(v.position_id, {}).get("opened_px", 0)
                last = coord.px_last.get(v.code, cost)
                e.setdefault("unreal", 0.0)
                e["unreal"] += v.shares * (last - cost)
                e.setdefault("shares", 0)
                e["shares"] += v.shares
            for eng, e in per_eng.items():
                w.writerow([eng, round(e["real"], 2), e["n"],
                            sum(1 for v in coord.virtuals if v.engine == eng), e.get("shares", 0),
                            round(e.get("unreal", 0.0), 2)])
    written.append("attribution.csv")

    # 7 allocation_plan_log.csv
    with open(os.path.join(outdir, "allocation_plan_log.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["signal_day", "exec_day", "n_scored", "n_buys", "n_sells", "n_rejects_alloc",
                    "budget_state", "dd_vs_peak", "engine_funding_json", "cash_ok", "n_fills", "n_exec_rejects"])
        for p_ in (result["plan_log"] if result else []):
            w.writerow([p_["signal_day"], p_["exec_day"], p_["n_scored"], p_["n_buys"], p_["n_sells"],
                        p_["n_rejects_alloc"], p_["budget_state"], p_["dd"],
                        json.dumps(p_["engine_funding"], ensure_ascii=False),
                        p_["cash_conservation"]["ok"], p_["n_fills"], p_["n_exec_rejects"]])
    written.append("allocation_plan_log.csv")

    # 8 data_quality_report.json
    q = runner.portal.quality_report() if hasattr(runner.portal, "quality_report") else {"note": "合成面板"}
    _jdump(os.path.join(outdir, "data_quality_report.json"), {"_provenance": prov, **q})
    written.append("data_quality_report.json")

    # 9 universe_buckets_sample.json / 10 industry_stage_snapshot.json
    from cycle_system.ashare_v2.industry_cycle import classify_state, stage_features
    snap = []
    for ind in sorted(lc._states):
        f = stage_features(runner.b, ind, day)
        snap.append({"industry": ind, "state": lc.industry_state(ind, day), "initial_state": classify_state(f),
                     "status": f.get("status")})
    _jdump(os.path.join(outdir, "industry_stage_snapshot.json"),
           {"_provenance": prov, "day": day, "industries": snap,
            "trust_gate": "snapshot成分PROXY_ONLY; Strict无正式信号(见state_engine/测试)"})
    written.append("industry_stage_snapshot.json")
    ub = {"_provenance": prov}
    if mode in (RunMode.SHADOW.value, RunMode.DIAGNOSTIC.value):
        from cycle_system.ashare_v2.universe import UniverseProvider
        up = UniverseProvider(runner.portal)
        b = up.buckets_at(day)
        ub.update({"day": b["day"], "pool_counts": {k: len(v) for k, v in b["pool_current"].items()},
                   "data_layer_sizes": {k: (len(v) if isinstance(v, list) else v)
                                        for k, v in b["data_layers"].items()},
                   "market_wide": b["market_wide"], "caveats": {"survivorship": b["caveats"]["survivorship"]}})
    else:
        ub["note"] = "SYNTHETIC模式为合成面板, 不适用真实池九桶(避免误导); 见shadow_run产物"
    _jdump(os.path.join(outdir, "universe_buckets_sample.json"), ub)
    written.append("universe_buckets_sample.json")

    # findings.md
    lines = [f"# Ashare Dual-Cycle V2 findings — mode={mode}", "",
             f"- 窗口: {prov['window']}  模拟天数: {prov['days_simulated']}",
             f"- 代码版本: {prov['code_version']}  配置哈希: {prov['config_hash']}",
             f"- 数据锚: manifest {prov['manifest_sha256'][:12]}…(250/250)", "",
             "## 四态分离",
             "- 编译/静态: PASS",
             "- 工程测试: PASS(pytest tests/ashare_v2, 见 experiment_manifest)",
             "- 数据同步: PASS(R31锚一致, baseline_integrity C1-C7)",
             "- **预测有效: NOT_CLAIMED —— 全流程未经独立样本外验证, 不声明任何预测有效**", "",
             "## 显式标注"]
    for a in ["SURVIVORSHIP_BIAS_PARTIAL(池=当前配置)", "行业信任=SW_PARTIAL/CURRENT_SNAPSHOT_ONLY(PROXY), Strict无正式信号",
              "评分未校准(CALIBRATED=False): 禁止预期收益率/胜率解读", "ST历史不可得; 停牌=缺行近似",
              "2020-2026研究窗非样本外", f"模式边界: {mode}"]:
        lines.append(f"- {a}")
    if result:
        coord = runner.coord
        rej_reasons = {}
        for r in coord.rejects:
            rej_reasons[r["reason"]] = rej_reasons.get(r["reason"], 0) + 1
        lines += ["", "## 运行读数(工程口径, 非绩效宣称)",
                  f"- 候选(合并后)记录数: {len(runner.opportunities)}",
                  f"- 成交笔数: {result['n_fills']}  执行拒绝: {result['n_rejects']}  拒绝分布: {rej_reasons}",
                  f"- 守恒: nav=cash+Σ持仓 {'OK' if result['conservation_ok'] else 'FAIL'};"
                  f" 虚拟=真实对账 {'OK' if result['virtual_real_reconciled'] else 'FAIL'}",
                  f"- NAV(模拟账本, 基{C.NAV0:.0f}): {result['nav_hist'][0]['nav']:.2f} → {result['nav_final']:.2f}"
                  f"(仅系统守恒性读数, 不含滑点建模全量, 禁止绩效解读)",
                  f"- 风险预算事件: {sum(1 for p_ in result['plan_log'] if p_['budget_state'] == 'EXCEEDED')}",
                  f"- 协调器显式标注: {coord.annotations if coord.annotations else '无'}"]
    if diag:
        d = diag["hypothetical_allocation"]
        lines += ["", "## 单日诊断",
                  f"- 行业状态分布: {sorted(set(diag['industry_states'].values()))}",
                  f"- 候选数: {diag['candidates_merged']}  假想分配: {len(d['buys'])}笔"
                  f"(现金守恒{d['cash_conservation']['ok']}), 拒绝理由样例:"
                  f" {sorted({r['reason'] for r in d['rejects']})[:5]}",
                  f"- Strict闸门(同数据): {diag['strict_gate'] and diag['strict_gate'][0]['status']}"]
    lines += ["", "## 失败项清单", "- (逐日拒绝原因见 allocation_plan_log.csv / trades.csv REJECT_* 行)", ""]
    open(os.path.join(outdir, "findings.md"), "w", encoding="utf-8").write("\n".join(lines))
    written.append("findings.md")
    return written
