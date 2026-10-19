# -*- coding: utf-8 -*-
"""forward_shadow — 前向SHADOW每日CLI (独立验收第6项; 第二轮复核P0修复版)。

用法:
  python -X utf8 cycle_system/ashare_v2/tools/forward_shadow.py fetch  2026-10-19   # 增量层抓取(收盘后)
  python -X utf8 cycle_system/ashare_v2/tools/forward_shadow.py plan    2026-10-19
  python -X utf8 cycle_system/ashare_v2/tools/forward_shadow.py execute 2026-10-20
  python -X utf8 cycle_system/ashare_v2/tools/forward_shadow.py verify

纪律(第二轮复核):
  * P0-1 执行价: execute不使用T+1收盘价。默认 DELAYED_REBUILD_RAW_OPEN —— 收盘后用增量层
    当日已固定的raw open重建成交, 独立标注 execution_mode, 不声称09:40实时执行;
    raw open在09:25集合竞价后即固定, 收盘后重建无时间穿越。涨跌停/费用域全部raw口径。
  * P0-2 数据: 冻结R31 CSV永不改写; 新交易日只进 forward/incremental/(逐日元数据:
    fetched_at/source/sha256/缺券清单), plan要求收盘后抓取, 校验不可静默关闭。
  * P0-3 计划: T日create_plan后不可变; T+1现金约束只产生 parent_plan_hash 关联的缩减记录
    (portfolio.adjust_log), 增股/增预算/换码在结构上不可能(见 _rebudget_pending_buys 单调断言)。
  * 时间证明: 每条FORWARD记录带 wall_clock(生成时刻)+data_fetched_at(行情可见时刻)+
    prediction_module_hashes(与FREEZE逐模块比对, 失配=拒绝FORWARD)+config_hash+code_version;
    预测模块与辅助工具分开审计(工具变更落 tool_changes, 不静默覆盖R0)。
  * 幂等: 同signal_day重复plan、无plan/重复execute、增量缺失 → 全部显式拒绝; 中断恢复=
    先verify再动作。链头独立存档 chain_head.json(整链重写可被比对发现; 非不可篡改存储宣称)。
  * 正式账本门: 默认 DIAGNOSTIC_REHEARSAL 标注(第二轮裁定: 修复验证前暂停写入正式FORWARD
    绩效账本); --formal 且准入标志文件存在时才打 FORMAL 标。
  * 状态域统一(第三轮§6-2修复): _state_domain() 令 plan 与 execute 在 DIAGNOSTIC 下**读写同一
    rehearsal 域**——T+1 成交必然进入下一交易日 plan 的初始状态(此前 plan 写正式 state 而
    execute 写 rehearsal, 存在连续运行分叉); 准入前正式 state/正式账本全程零写入, 由连续三
    交易日 plan→execute→plan→execute 实弹测试硬断言(test_rehearsal_three_day_state_continuity)。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                   # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                             # noqa: E402
from cycle_system.ashare_v2 import forward_ingest as FI                    # noqa: E402
from cycle_system.ashare_v2 import forward_ledger as FL                     # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                   # noqa: E402
from cycle_system.ashare_v2.portfolio import PortfolioCoordinator            # noqa: E402
from cycle_system.ashare_v2.runner import Runner                            # noqa: E402
from cycle_system.ashare_v2.schemas import RunMode                          # noqa: E402

FWD_DIR = os.path.join(C.OUTPUT_DIR, "forward")
CHAIN_P = os.path.join(FWD_DIR, "forward_chain.jsonl")
HEAD_P = os.path.join(FWD_DIR, "chain_head.json")
STATE_P = os.path.join(FWD_DIR, "coord_state.json")
FREEZE_P = os.path.join(FWD_DIR, "FREEZE.json")
ADMIT_P = os.path.join(FWD_DIR, "FORMAL_ADMIT.json")
LEDGER_NAME = "dual_cycle_v2_forward"
LEDGER_NAME_REHEARSAL = "dual_cycle_v2_fwd_rehearsal"      # 准入前成交演练专用: 正式账本零写入
TZ = timezone(timedelta(hours=8))


def _state_rehearsal_p() -> str:
    """运行期解析(随FWD_DIR): 禁止模块级冻结路径——否则tmp环境会污染真实目录。"""
    return os.path.join(FWD_DIR, "coord_state_rehearsal.json")

# 预测模块(冻结判据所在): 失配=拒绝FORWARD。辅助工具单列(变更须记录但不阻断, 分开审计)。
PREDICTION_MODULES = (
    "config.py", "schemas.py", "data_portal.py", "universe.py", "industry_cycle.py",
    "long_cycle.py", "fast_v.py", "exit_manager.py", "opportunity_ranker.py",
    "signal_merge.py", "capital_allocator.py", "portfolio.py", "runner.py",
    "forward_ledger.py", "forward_ingest.py", "report.py",
)


def _sha(p: str) -> str:
    import hashlib
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _freeze():
    return json.load(open(FREEZE_P, encoding="utf-8")) if os.path.exists(FREEZE_P) else None


def _audit_baseline() -> dict:
    """R0 + 全部addendum(R0.1…)按版本序合并的模块哈希基线; R0原件永不改写。"""
    base = dict((_freeze() or {}).get("module_hashes", {}))
    ads = sorted(f for f in os.listdir(FWD_DIR) if f.startswith("FREEZE_ADDENDUM_")) if os.path.isdir(FWD_DIR) else []
    for ad in ads:
        base.update(json.load(open(os.path.join(FWD_DIR, ad), encoding="utf-8")).get("module_hashes", {}))
    return base


def _module_audit() -> dict:
    """逐模块哈希(与freeze_manifest同式全64位): 预测类对照基线(失配raise),
    工具类变更落tool_changes(分开审计, 不静默覆盖R0宣称)。"""
    fmods = _audit_baseline()
    base = os.path.join(C.REPO_ROOT, "cycle_system")
    now, mism, tool_changes = {}, [], []
    for root, _dirs, files in os.walk(os.path.join(base, "ashare_v2")):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            rel = "ashare_v2/" + os.path.relpath(os.path.join(root, fn), os.path.join(base, "ashare_v2")).replace("\\", "/")
            h = _sha(os.path.join(root, fn))
            now[rel] = h
            if fmods.get(rel) and fmods[rel] != h:
                if "ashare_v2/" + rel.split("/", 1)[1] in tuple("ashare_v2/" + m for m in PREDICTION_MODULES):
                    mism.append(rel)
                else:
                    tool_changes.append(rel)
    if mism:
        raise RuntimeError(f"预测模块与FREEZE失配(禁止FORWARD记账, 须先裁定): {mism}")
    return {"prediction_verified": [f"ashare_v2/{m}" for m in PREDICTION_MODULES if f"ashare_v2/{m}" in now],
            "tool_changes": tool_changes, "modules_hashed": len(now)}


def _prov() -> dict:
    def git(*a):
        return subprocess.run(["git", "-C", C.REPO_ROOT] + list(a), capture_output=True,
                              text=True).stdout.strip()
    return {"code_version": git("rev-parse", "HEAD"), "config_hash": C.config_hash(),
            "wall_clock": datetime.now(TZ).isoformat()}


def _chain_has(event: str, key: str, val: str) -> bool:
    if not os.path.exists(CHAIN_P):
        return False
    return any(r.get("event") == event and r.get(key) == val for r in FL.read_all(CHAIN_P))


def _seal_head():
    v = FL.verify(CHAIN_P)
    last = FL.last_state(CHAIN_P)
    head = {"ok": v["ok"], "n": v["n"], "seq": last[0] if v["ok"] else None,
            "hash": last[1] if v["ok"] else None, "at": datetime.now(TZ).isoformat()}
    prev = None
    if os.path.exists(HEAD_P):
        prev = json.load(open(HEAD_P, encoding="utf-8"))
    if prev and not head["ok"]:
        raise RuntimeError(f"链验证失败且存在旧链头存档(疑似整链重写, 拒绝继续): prev={prev}")
    with open(HEAD_P, "w", encoding="utf-8") as f:
        json.dump(head, f, ensure_ascii=False, indent=1)
    return head


def _run_kind(day: str) -> str:
    fr = _freeze()
    if not fr or day <= fr.get("frozen_trade_date", "9999"):
        return "REPLAY"
    return "FORWARD"


def _merged_portal(through: str):
    """冻结层(原verify_hashes) + 增量层合并; 增量=冻结快照末日之后已审计的日数据。"""
    portal = DataPortal(data_mode=C.PIT_RESEARCH)     # 校验不关闭
    df = portal.price_panel()
    import pandas as pd
    frozen_max = str(pd.Timestamp(df["date"].max()).date())
    days = []
    d = pd.Timestamp(frozen_max) + timedelta(days=1)
    end = pd.Timestamp(through)
    while d <= end:
        ds = str(d.date())
        if os.path.exists(os.path.join(FI.ING_DIR, f"{ds}.csv")):
            days.append(ds)
        d += timedelta(days=1)
    return FI.merge_with_frozen(df, days), frozen_max, days


class MergedPortal:
    """冻结层+增量层合并面板(鸭子型DataPortal最小接口): 引擎/编排全部读同一合并视图。"""

    def __init__(self, df):
        self._df = df

    def price_panel(self):
        return self._df


def _ledger_last_date(ledger_name: str) -> str | None:
    p = os.path.join(FWD_DIR, "ledger", f"ledger_{ledger_name}.jsonl")
    if not os.path.exists(p):
        return None
    last = None
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            d = r.get("execution_date") or r.get("date") or r.get("day")
            if d:
                last = max(str(last), str(d)[:10]) if last else str(d)[:10]
    return last


def _state_domain(formal: bool):
    """状态/账本单一域(审查§6-2分叉修复): plan与execute必须读写同一域——
    FORMAL准入前两者都进rehearsal state+rehearsal ledger, 正式state/账本保持只读冻结;
    否则出现"execute成交进rehearsal而次日plan读正式state(无成交)"的计划-持仓分叉。"""
    gate = "FORMAL" if (formal and os.path.exists(ADMIT_P)) else "DIAGNOSTIC_REHEARSAL"
    rep = _state_rehearsal_p()
    if gate == "FORMAL":
        return gate, STATE_P, STATE_P, LEDGER_NAME
    src = rep if os.path.exists(rep) else STATE_P          # 首跑从正式state快照初始化(只读)
    return gate, src, rep, LEDGER_NAME_REHEARSAL


def cmd_plan(day: str, formal: bool = False, recover: bool = False) -> int:
    import pandas as pd
    gate, state_src, state_dst, lname = _state_domain(formal)
    if _chain_has("PLAN", "signal_day", day) and not recover:
        st = {}
        if os.path.exists(state_src):
            try:
                st = json.load(open(state_src, encoding="utf-8"))
            except ValueError:
                st = {}
        if not st or day not in st.get("plans", {}):
            # 保守崩溃检测: state缺失或plan未落, 都按"链有PLAN但state未落"处理(防幂等漏检)
            print(json.dumps({"error": f"链上已有{day}的PLAN但state未落(崩溃窗口): 禁止重复plan, "
                                       "用 plan <day> --recover 重建state(链记PLAN_RECOVERY, 不重复PLAN)",
                              "code": "PLAN_CRASH_STATE_MISSING"}, ensure_ascii=False))
            return 7
        print(json.dumps({"error": f"{day} 已有PLAN记录(幂等拒绝; 重跑须先裁定)", "code": "IDEMPOTENT_PLAN"},
                         ensure_ascii=False))
        return 3
    if recover and not _chain_has("PLAN", "signal_day", day):
        print(json.dumps({"error": f"--recover要求链上存在{day}的PLAN", "code": "NO_PLAN_TO_RECOVER"},
                         ensure_ascii=False))
        return 3
    if os.path.exists(CHAIN_P):
        v = FL.verify(CHAIN_P)
        if not v["ok"]:
            print(json.dumps({"error": f"链失序(seq {v['broken_at']}), 拒绝写入: 先人工恢复", "code": "CHAIN_BROKEN"},
                             ensure_ascii=False))
            return 4
    panel, frozen_max, inc_days = _merged_portal(day)
    import pandas as _pd
    if _pd.Timestamp(day) > _pd.Timestamp(frozen_max):
        try:
            meta = FI.require_plan_ready(day)             # FORWARD/增量日: 收盘后抓取证明(P0-2)
        except (FileNotFoundError, RuntimeError) as e:
            print(json.dumps({"error": str(e), "code": "INCREMENT_NOT_READY"}, ensure_ascii=False))
            return 6
    else:
        meta = {"fetched_at": "frozen-layer", "source": "R31 frozen manifest(verify_hashes通过)",
                "csv_sha256": "frozen", "codes_missing": [], "note": "REPLAY演练日在冻结层内, 无需增量"}
    if _pd.Timestamp(day) > _pd.Timestamp(str(panel["date"].max())):
        print(json.dumps({"error": f"{day} 无增量数据: 先 fetch {day}"}, ensure_ascii=False))
        return 2
    r = Runner(RunMode.SHADOW.value, portal=MergedPortal(panel),
               ledger_root=os.path.join(FWD_DIR, "ledger"), ledger_name=lname)
    r.setup()
    if os.path.exists(state_src):
        r.coord = PortfolioCoordinator.load_state(state_src, nav0=C.NAV0,
                                                  ledger_root=os.path.join(FWD_DIR, "ledger"),
                                                  ledger_name=lname)
    scored = r._scored_for(pd.Timestamp(day))
    pxm, volm, _ = r._day_maps(pd.Timestamp(day))
    px_valid = {c: v for c, v in pxm.items() if v}
    ctx_vol = {c: v for c, v in volm.items() if v and pxm.get(c)}
    net = r.coord.plan_day(day, [s for s in scored if s["code"] in px_valid], px_valid,
                           ctx_vol, r.sec_of,
                           exit_ctx_fn=lambda c, d: r._exit_ctx(c, pd.Timestamp(day)))
    run_kind = _run_kind(day)
    audit = _module_audit() if run_kind == "FORWARD" else {"prediction_verified": [], "tool_changes": [],
                                                           "modules_hashed": 0, "note": "REPLAY不强制模块审计"}
    rec = {"event": "PLAN_RECOVERY" if recover else "PLAN", "signal_day": day, **_prov(),
           "recovers_plan_of": day if recover else None,
           "run_kind_expected": run_kind,
           "data_frozen_through": frozen_max, "data_incremental_days": inc_days,
           "data_fetched_at": meta["fetched_at"], "data_source": meta["source"],
           "data_csv_sha256": meta["csv_sha256"], "data_missing_codes": meta["codes_missing"],
           "module_audit": audit,
           "formal_gate": gate,
           "ledger_target": (f"FORMAL:{lname}" if gate == "FORMAL"
                             else f"REHEARSAL:{lname}(正式账本与正式state零写入)"),
           "industry_states": {ind: r.lc.industry_state(ind, day) for ind in r.lc._states},
           "candidates": [{"code": m["merged"]["code"], "engines": m["merged"]["engines"],
                           "stage": m["score"]["cycle_stage"], "pts": m["score"]["score_points"],
                           "cred": next((x.data_credibility for x in m["merged"]["records"]), ""),
                           "trust": next((x.membership_trust for x in m["merged"]["records"]), "")}
                          for m in r.opportunities if m["day"] == day],
           "sells": [{"code": s["code"], "shares": s["shares"], "engine": s["engine"],
                      "clause": s["clause"], "risk_forced": s["risk_forced"],
                      "plan_hash": s.get("plan", {}).get("plan_hash")} for s in net.sells],
           "buys": [{"code": b["code"], "budget": b["cash_budget"], "engines": b["engines"],
                     "plan_hash": b.get("plan", {}).get("plan_hash")} for b in net.buys],
           "budget_state": r.coord.plans[day]["budget_state"], "dd": r.coord.plans[day]["dd"],
           "nav_est": r.coord.nav(px_valid)}
    FL.append_record(CHAIN_P, rec, freeze=_freeze())
    r.coord.save_state(state_dst)
    head = _seal_head()
    print(json.dumps({"plan": day, "sells": len(net.sells), "buys": len(net.buys),
                      "run_kind": rec.get("run_kind") or run_kind,
                      "formal_gate": rec["formal_gate"], "chain_seq": head["seq"],
                      "head_hash": (head["hash"] or "")[:16]}, ensure_ascii=False))
    return 0


def cmd_execute(day: str, formal: bool = False) -> int:
    """T+1延迟重建成交(raw open)。

    裁定合规: 正式FORWARD绩效账本(ledger_dual_cycle_v2_forward)在准入(FORMAL_ADMIT.json+
    --formal)之前**零写入**——默认全部成交进 rehearsal 账本与rehearsal state(从正式state
    快照初始化), 正式状态/账本保持"从未自动成交"的原始性; 链记录(execution audit)始终写。"""
    import pandas as pd
    gate, state_src, state_dst, ledger_name = _state_domain(formal)
    if not os.path.exists(state_src):
        print(json.dumps({"error": "先执行 plan(信号日)才能 execute"}, ensure_ascii=False))
        return 2
    if _chain_has("EXECUTE", "exec_day", day):
        print(json.dumps({"error": f"{day} 已有EXECUTE记录(幂等拒绝)", "code": "IDEMPOTENT_EXECUTE"},
                         ensure_ascii=False))
        return 3
    led_last = _ledger_last_date(ledger_name)
    if led_last and led_last >= day:
        print(json.dumps({"error": f"账本({ledger_name})已推进至{led_last}≥{day}但链上无该日EXECUTE记录"
                                   "(半执行崩溃窗口): 禁止自动重放(会双记账), 须人工对账后裁定",
                          "code": "LEDGER_ADVANCED_NO_RECORD"}, ensure_ascii=False))
        return 7
    now = datetime.now(TZ)
    if str(now.date()) == day and now.strftime("%H:%M") < "15:00":
        print(json.dumps({"error": f"重建须在{day}收盘后(现{now.isoformat()[:16]}): 盘中拒绝",
                          "code": "NOT_AFTER_CLOSE"}, ensure_ascii=False))
        return 5
    coord = PortfolioCoordinator.load_state(state_src, nav0=C.NAV0,
                                            ledger_root=os.path.join(FWD_DIR, "ledger"),
                                            ledger_name=ledger_name)
    signal_day = next((d for d, r_ in coord.plans.items() if r_["exec_day"] == day), None)
    if signal_day is None:
        print(json.dumps({"error": f"无信号日的计划以{day}为执行日"}, ensure_ascii=False))
        return 2
    if _run_kind(signal_day) == "FORWARD":
        _module_audit()                                # 执行记账同样验预测模块哈希
    try:
        inc = FI.load_day(day)                             # raw OHLC(增量层, 哈希已验)
    except (FileNotFoundError, ValueError) as e:
        print(json.dumps({"error": str(e), "code": "INCREMENT_NOT_READY"}, ensure_ascii=False))
        return 6
    df_inc = inc.set_index("code")
    px1 = {c: float(v) for c, v in df_inc["open"].to_dict().items() if v == v and v > 0}
    frozen = DataPortal(data_mode=C.PIT_RESEARCH).price_panel()
    pxc = frozen[frozen["date"] == pd.Timestamp(signal_day)]
    if not len(pxc):
        inc_s = None
        try:
            inc_s = FI.load_day(signal_day)
        except Exception:                              # noqa: BLE001
            pass
        if inc_s is None:
            print(json.dumps({"error": f"信号日{signal_day}收盘价不可得(冻结层/增量层均无)"},
                             ensure_ascii=False))
            return 2
        pxc = inc_s
    pxc = {c: float(v) for c, v in pxc.set_index("code")["close"].to_dict().items()
           if v == v and v > 0}
    if not px1:
        print(json.dumps({"error": f"{day} 增量层无raw open"}, ensure_ascii=False))
        return 2
    fills, rejects = coord.execute_day(signal_day, px1, pxc)
    run_kind = _run_kind(signal_day)
    rec = {"event": "EXECUTE", "signal_day": signal_day, "exec_day": day, **_prov(),
           "execution_mode": "DELAYED_REBUILD_RAW_OPEN",
           "price_basis": "raw open(执行/涨跌停/费用域), 非close_adj — P0-1修复",
           "formal_gate": gate,
           "ledger_target": (f"FORMAL:{ledger_name}" if gate == "FORMAL"
                             else f"REHEARSAL:{ledger_name}(正式账本与正式state零写入)"),
           "data_fetched_at": json.load(open(os.path.join(FI.ING_DIR, f"{day}.meta.json"),
                                             encoding="utf-8"))["fetched_at"],
           "fills": [{k: f.get(k) for k in ("code", "action", "shares", "price", "notional",
                                            "fees", "plan_hash")} for f in fills],
           "rejects": rejects,
           "plan_adjustments": [a for a in coord.adjust_log if a["signal_day"] == signal_day],
           "nav": coord.nav(px1), "cash": round(coord.ledger.cash, 4),
           "positions": {c: p["total"] for c, p in coord.ledger.pos.items()}}
    FL.append_record(CHAIN_P, rec, freeze=_freeze())
    coord.save_state(state_dst)
    head = _seal_head()
    print(json.dumps({"execute": day, "mode": rec["execution_mode"], "gate": rec["formal_gate"],
                      "ledger_target": rec["ledger_target"],
                      "fills": len(fills), "rejects": len(rejects),
                      "adjustments": len(rec["plan_adjustments"]),
                      "nav": rec["nav"], "chain_seq": head["seq"]}, ensure_ascii=False))
    return 0


def cmd_verify() -> int:
    v = FL.verify(CHAIN_P)
    recs = FL.read_all(CHAIN_P)
    counts, gates = {}, {}
    for x in recs:
        k = x.get("run_kind") or x.get("run_kind_expected") or "?"
        counts[k] = counts.get(k, 0) + 1
        g = x.get("formal_gate", "-")
        gates[g] = gates.get(g, 0) + 1
    head = json.load(open(HEAD_P, encoding="utf-8")) if os.path.exists(HEAD_P) else None
    last = FL.last_state(CHAIN_P)
    sealed = bool(head and head.get("ok") and v["ok"] and head.get("seq") == v["n"]
                  and head.get("hash") == last[1])    # 头哈希逐字节比对: 整链重写(重算哈希)不符即报警
    print(json.dumps({"chain": v, "records": len(recs), "run_kind_counts": counts,
                      "formal_gate_counts": gates, "chain_head_seal": head,
                      "head_matches_chain": sealed}, ensure_ascii=False))
    if not sealed:
        return 1
    return 0 if v["ok"] else 1


def cmd_seal() -> int:
    """一次性/恢复性链头封存: 仅链verify通过时按当前内容写head(既有链接入审计的起点)。"""
    v = FL.verify(CHAIN_P)
    if not v["ok"]:
        print(json.dumps({"error": f"链未通过验证, 拒绝封存: {v['broken_at']}"}, ensure_ascii=False))
        return 1
    head = _seal_head()
    print(json.dumps({"sealed": head}, ensure_ascii=False))
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if a not in ("--formal", "--recover")]
    formal = "--formal" in sys.argv
    recover = "--recover" in sys.argv
    if len(args) < 2 and args[0:1] != ["seal"]:
        print(__doc__)
        return 2
    cmd = args[0]
    if cmd == "seal":
        return cmd_seal()
    day = args[1]
    if cmd == "fetch":
        m = FI.fetch_day(day)
        print(json.dumps({"fetched": day, "rows": m["rows"], "missing": len(m["codes_missing"]),
                          "sha256": m["csv_sha256"][:16]}, ensure_ascii=False))
        return 0
    if cmd == "plan":
        return cmd_plan(day, formal=formal, recover=recover)
    if cmd == "execute":
        return cmd_execute(day, formal=formal)
    if cmd == "verify":
        return cmd_verify()
    print(f"未知命令: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
