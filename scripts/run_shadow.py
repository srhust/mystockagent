# -*- coding: utf-8 -*-
"""正式每日前向影子驱动入口(只读, 无交易接口)。用法: python scripts/run_shadow.py
特性: 重复启动安全(无新交易日直接退出)/漏日拒绝/已登记成交的修订冲突检测(重算哈希不符即拒绝静默恢复)/
PLAN_EXECUTED存逐笔成交/状态哈希链断言/真实首日自动签发正式启动凭证。"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cycle_system"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import _path  # noqa: F401

import numpy as np
import pandas as pd

import config
from bottom_nowcaster import build_features
import mu_af_v2 as M
from cycle_portfolio import run_contract, HOLD, plan_next_day, apply_day, _enc, _dec, state_hash, save_state, load_state

D = os.path.join(config.DATA_DIR, "mu")
COST, DEDUP = 0.0015, 60
TICKERS = ["MU", "WDC", "STX", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "TER", "FCX", "CAT", "QCOM", "TXN", "INTC"]
FWD = r"E:\mystockagent\output\forward"
CFGX = {"scorer": "12m/edge", "cost": COST, "dedup": DEDUP}


def load_market():
    closes, dp_ev, v4_ev, ma100s = {}, {}, {}, {}
    for sym in TICKERS:
        c = pd.read_csv(os.path.join(D, f"{sym}.csv"), parse_dates=["date"]).sort_values("date").set_index("date")["close"].astype(float)
        f = build_features(c, [])
        dd = f["dd252"]
        ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
        state250 = (dd <= -0.35).rolling(250, min_periods=1).max().astype(bool).values
        dp_ev[sym] = (((c > ma20) & (ma20 > ma20.shift(5)) & state250 & (dd <= -0.40) & (c <= ma50 * 1.05)) | ((ma50 > ma50.shift(10)) & (dd >= -0.30) & (dd <= -0.10)))
        ma100s[sym] = c.rolling(100).mean()
        closes[sym] = c
    v4tr = (M.v4.reindex(closes["MU"].index).fillna(0) > 0) & (M.v4.reindex(closes["MU"].index).fillna(0).diff() > 0)
    v4_ev["MU"] = v4tr

    def events(sym):
        rw = dp_ev[sym].values.copy()
        if sym in v4_ev:
            rw = rw | v4_ev[sym].values
        out, last = set(), -9999
        for i, e in enumerate(rw):
            if e and i - last >= DEDUP:
                out.add(i)
                last = i
        return out

    ev_all = {s: events(s) for s in TICKERS}
    OWNPOS = {s: pd.Series(np.arange(len(closes[s])), index=closes[s].index) for s in TICKERS}
    idx_all = pd.concat(closes.values()).index.unique().sort_values()
    PX = pd.DataFrame({s: closes[s].reindex(idx_all) for s in TICKERS}).ffill()
    LISTED = pd.DataFrame({s: closes[s].reindex(idx_all).notna() for s in TICKERS})
    PXd = {s: pd.Series(PX[s].values, index=idx_all) for s in TICKERS}
    MA100d = {s: pd.Series(ma100s[s].reindex(idx_all).values, index=idx_all) for s in TICKERS}
    LISTd = {s: LISTED[s] for s in TICKERS}

    def ledger_nav(cc, p):
        cash, shares = 100.0, 0.0
        nav = pd.Series(np.nan, index=cc.index)
        pv = p.reindex(cc.index).fillna(0.0).values
        pxs2 = cc.values
        ip = False
        for i in range(1, len(cc)):
            want = bool(pv[i - 1] >= 0.5)
            if want and not ip:
                shares, cash, ip = cash * (1 - COST) / pxs2[i], 0.0, True
            elif (not want) and ip:
                cash, shares, ip = shares * pxs2[i] * (1 - COST), 0.0, False
            nav.iloc[i] = cash + shares * pxs2[i]
        return nav.ffill().fillna(100.0)

    POSO = {}
    for s in TICKERS:
        ev2 = ev_all[s]
        cc = closes[s]
        nn = len(cc)
        p = np.zeros(nn)
        ip = False
        e_i = -999
        for i in range(nn):
            if not ip and i in ev2:
                ip, e_i = True, i
            elif ip and i - e_i >= HOLD:
                ip = False
            p[i] = 1.0 if ip else 0.0
        POSO[s] = pd.Series(p, index=cc.index)
    NAV = {s: ledger_nav(closes[s], POSO[s].reindex(closes[s].index)) for s in TICKERS}
    MCLOSE = PX.resample("ME").last()
    months = MCLOSE.index
    SMR = pd.DataFrame({s: NAV[s].reindex(idx_all).resample("ME").last().pct_change() for s in TICKERS}).reindex(months).fillna(0.0)

    def score(sym, mi, win, kind):
        w = SMR[sym].iloc[max(0, mi - win):mi]
        if len(w) < win:
            return np.nan
        eqw = (1 + w).cumprod()
        mdd = float((eqw / eqw.cummax() - 1).min())
        return float(eqw.iloc[-1]) / abs(mdd) if mdd else np.nan

    MI0 = 72
    sd0 = idx_all[idx_all.to_period("M") == months[MI0].to_period("M")][0]
    days = idx_all[idx_all.get_loc(sd0):]
    SNAP = {}
    for mi in range(1, len(months)):
        for s in TICKERS:
            SNAP[(months[mi].to_period("M"), s)] = score(s, mi, 12, "edge")
    return dict(px=PXd, ma=MA100d, ev=ev_all, listed=LISTd, own=OWNPOS, days=days, snap=SNAP)


def serialize_fills(fills):
    """唯一成交序列化口径: 写盘与恢复比较共用(P0: 避免Timestamp/浮点原值与格式化值不等导致的假冲突)。"""
    return [[s, str(sig), str(fd), act, round(q, 6), round(p, 4), round(fee, 6)] for s, sig, fd, act, q, p, fee in fills]


def nav_of(state, px_d):
    """P0: 完整账户NAV = 现金+储备+Σ持仓×估值价(T收盘, 缺价用stale)。"""
    sd = state["last_date"]
    total = float(state["cash"]) + sum(state["reserve"].values())
    for s, q in state["shares"].items():
        if q > 0:
            pv = px_d[s].get(sd, np.nan)
            total += q * (pv if np.isfinite(pv) else state["stale"].get(s, np.nan))
    return total


def shadow_run(mkt, hold, state_path, log_path, crash_after_plan_at=None, crash_after_exec_at=None):
    """每日影子循环: 计划落盘→执行→EXECUTED(逐笔)落盘→状态存盘。已登记成交重算不符→修订冲突拒绝。"""
    px_d, ma100_d, ev_d, listed_d, ownpos_d, days_all, snap = mkt["px"], mkt["ma"], mkt["ev"], mkt["listed"], mkt["own"], mkt["days"], mkt["snap"]
    if os.path.exists(state_path):
        st = load_state(state_path)
    else:
        raise SystemExit(f"状态文件缺失: {state_path}(首次需先以bootstrap初始化)")
    done, created_ids, executed_recs, pending_plan = set(), set(), {}, None
    if os.path.exists(log_path):
        for l in open(log_path, encoding="utf-8"):
            r = json.loads(l)
            if r.get("type") == "PLAN_EXECUTED":
                done.add(r["plan_id"])
                executed_recs[r["plan_id"]] = r
            elif r.get("type") == "PLAN_CREATED":
                created_ids.add(r["plan_id"])
                if r["plan_id"] not in done:
                    pending_plan = _dec(r["plan"])
    pos0 = list(days_all).index(st["last_date"])
    todo = list(days_all[pos0 + 1:])
    if len(todo) == 0:
        return "no_new_trading_day"
    for j, d in enumerate(todo):
        if pending_plan is not None and pending_plan["date"] == d:
            pp = pending_plan
            pending_plan = None
        else:
            pp = plan_next_day(TICKERS, snap, "V2aF", px_d, ma100_d, ev_d, ownpos_d, st, d, hold_days=hold, calendar=days_all)
        if pp["plan_hash"] not in created_ids:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "PLAN_CREATED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "plan": _enc(dict(pp))}, default=str) + "\n")
            created_ids.add(pp["plan_hash"])
        if crash_after_plan_at == j:
            return "crash_after_plan"
        ca = apply_day(TICKERS, snap, "V2aF", px_d, ma100_d, ev_d, listed_d, ownpos_d, st, pp, d, hold_days=hold, require_config=True)
        st = ca["end_state"]
        if pp["plan_hash"] in done:  # P0: 已登记成交 → 重算必须与已确认记录一致(行情修订检测)
            rec = executed_recs[pp["plan_hash"]]
            if state_hash(st) != rec["new_state_hash"] or serialize_fills(ca["fills"]) != rec["fills"]:
                raise AssertionError(f"数据修订冲突: {d} 重算状态哈希/成交与已登记PLAN_EXECUTED不一致, 拒绝静默恢复(生成修订事件, 人工裁决)")
        else:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "PLAN_EXECUTED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "fills": serialize_fills(ca["fills"]), "eq": round(float(ca["eq"].iloc[0]), 6), "fees_cum": round(st["fees"], 4), "new_state_hash": state_hash(st)}, default=str) + "\n")
        if crash_after_exec_at == j:
            return "crash_after_exec"
        save_state(st, state_path)
    # P1: 哈希链断言(不再只打印)
    crs = [json.loads(l) for l in open(log_path, encoding="utf-8") if json.loads(l)["type"] == "PLAN_CREATED"]
    exs = [json.loads(l) for l in open(log_path, encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
    cbd = {_dec(r["plan"])["date"].isoformat(): r for r in crs}
    exd = {r["date"]: r for r in exs}
    dates = sorted(exd)
    for i in range(len(dates) - 1):
        assert exd[dates[i]]["new_state_hash"] == _dec(cbd[dates[i + 1]]["plan"])["state_hash"], f"哈希链断裂@{dates[i]}"
    return "done"


def _load_run_ctx(hold, state_path, log_path):
    st = load_state(state_path)
    done, created, executed_recs, pending_plan = set(), set(), {}, None
    if os.path.exists(log_path):
        for l in open(log_path, encoding="utf-8"):
            r = json.loads(l)
            if r.get("type") == "PLAN_EXECUTED":
                done.add(r["plan_id"])
                executed_recs[r["plan_id"]] = r
            elif r.get("type") == "PLAN_CREATED":
                created.add(r["plan_id"])
                if r["plan_id"] not in done:
                    pending_plan = _dec(r["plan"])
    return st, done, created, executed_recs, pending_plan


def plan_phase(mkt, hold, state_path, log_path):
    """P0阶段一: T日收盘后生成并持久化T+1冻结计划(created_at真实生成时间, 之后不可覆盖)。只做紧邻下一交易日。"""
    px_d, ma100_d, ev_d, ownpos_d, days_all, snap = mkt["px"], mkt["ma"], mkt["ev"], mkt["own"], mkt["days"], mkt["snap"]
    st, done, created, _, pending_plan = _load_run_ctx(hold, state_path, log_path)
    pos0 = list(days_all).index(st["last_date"])
    todo = days_all[pos0 + 1:]
    if len(todo) == 0:
        return "no_new_trading_day"
    d = todo[0]
    if pending_plan is not None and pending_plan["date"] == d:
        return "plan_exists"
    pp = plan_next_day(TICKERS, snap, "V2aF", px_d, ma100_d, ev_d, ownpos_d, st, d, hold_days=hold, calendar=days_all)
    if pp["plan_hash"] not in created:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "PLAN_CREATED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "created_at": pd.Timestamp.now().isoformat(), "plan": _enc(dict(pp))}, default=str) + "\n")
    return "planned"


def settle_phase(mkt, hold, state_path, log_path, allow_replay=False):
    """P0阶段二: T+1收盘后读取此前已登记计划模拟成交; 计划缺失→PLAN_MISSING拒绝计分(allow_replay仅供bootstrap回放)。"""
    px_d, ma100_d, ev_d, listed_d, ownpos_d, days_all, snap = mkt["px"], mkt["ma"], mkt["ev"], mkt["listed"], mkt["own"], mkt["days"], mkt["snap"]
    st, done, created, executed_recs, _ = _load_run_ctx(hold, state_path, log_path)
    pos0 = list(days_all).index(st["last_date"])
    todo = days_all[pos0 + 1:]
    if len(todo) == 0:
        return "no_new_trading_day"
    d = todo[0]
    plan_rec = None
    if os.path.exists(log_path):
        for l in open(log_path, encoding="utf-8"):
            r = json.loads(l)
            if r.get("type") == "PLAN_CREATED" and r.get("date") == d.isoformat():
                plan_rec = r
    if plan_rec is None:
        if not allow_replay:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "PLAN_MISSING", "date": d.isoformat(), "scoring": False}, default=str) + "\n")
            return "plan_missing"
        pp = plan_next_day(TICKERS, snap, "V2aF", px_d, ma100_d, ev_d, ownpos_d, st, d, hold_days=hold, calendar=days_all)
        if pp["plan_hash"] not in created:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "PLAN_CREATED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "created_at": pd.Timestamp.now().isoformat(), "mode": "replay", "plan": _enc(dict(pp))}, default=str) + "\n")
    else:
        pp = _dec(plan_rec["plan"])  # P0: 消费事前计划
        body = {k: v for k, v in pp.items() if k != "plan_hash"}
        assert hashlib.sha1(json.dumps(_enc(body), sort_keys=True, default=str).encode()).hexdigest()[:12] == pp["plan_hash"], "事前计划验签失败"
    ca = apply_day(TICKERS, snap, "V2aF", px_d, ma100_d, ev_d, listed_d, ownpos_d, st, pp, d, hold_days=hold, require_config=True)
    st = ca["end_state"]
    if pp["plan_hash"] in done:
        rec = executed_recs[pp["plan_hash"]]
        if state_hash(st) != rec["new_state_hash"] or serialize_fills(ca["fills"]) != rec["fills"]:
            raise AssertionError(f"数据修订冲突: {d} 重算与已登记PLAN_EXECUTED不一致, 拒绝静默恢复")
    else:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "PLAN_EXECUTED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "phase_mode": "replay" if plan_rec is None else "forward", "fills": serialize_fills(ca["fills"]), "eq": round(float(ca["eq"].iloc[0]), 6), "fees_cum": round(st["fees"], 4), "new_state_hash": state_hash(st)}, default=str) + "\n")
    save_state(st, state_path)
    return "settled"


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    mkt = load_market()
    paths = {hold: (os.path.join(FWD, f"state_{hold}.json"), os.path.join(FWD, f"forward_log_{hold}.jsonl")) for hold in (90, 120)}
    ld90 = load_state(paths[90][0])["last_date"]
    ld120 = load_state(paths[120][0])["last_date"]
    assert ld90 == ld120, f"双配置进度不一致: 90={ld90} vs 120={ld120}, 拒绝运行"  # P0: 计分日期范围必须一致
    data_cutoff = str(mkt["days"][-1].date())
    if data_cutoff == str(ld90.date()):
        print(f"无新增交易日(截止{data_cutoff}), 退出")
        return
    start_navs = None
    if mode in ("settle", "auto"):
        start_navs = {hold: round(nav_of(load_state(paths[hold][0]), mkt["px"]), 6) for hold in (90, 120)}  # P0: 处理前冻结完整持仓估值NAV
        assert all(v > 0 for v in start_navs.values()), f"起点NAV无效: {start_navs}"
    if mode in ("plan", "auto"):
        print({hold: plan_phase(mkt, hold, *paths[hold]) for hold in (90, 120)})
    if mode in ("settle", "auto"):
        results = {hold: settle_phase(mkt, hold, *paths[hold], allow_replay=(mode == "auto")) for hold in (90, 120)}
        print(f"settle: {results} | 起点NAV(处理前冻结): {start_navs} | 数据截止: {data_cutoff}")
        cred_p = os.path.join(FWD, "launch_credential.json")
        if os.path.exists(cred_p) and results[90] == results[120] == "settled":
            cred = json.load(open(cred_p, encoding="utf-8"))
            fh = {f: hashlib.sha256(open(os.path.join(r"E:\mystockagent", f), "rb").read()).hexdigest()[:16] for f in ("cycle_system/cycle_portfolio.py", "scripts/run_shadow.py")}
            mismatch = [f for f, h in cred.get("executor_file_hashes", {}).items() if f in fh and fh[f] != h]
            if cred.get("forward_start") == "PENDING_FIRST_REAL_TRADING_DAY":
                assert not mismatch, f"执行器文件哈希与候选凭证不符{mismatch}, 拒绝签发(硬门槛)"  # P1: 不再仅警告
                first_day = mkt["days"][list(mkt["days"]).index(ld90) + 1]  # 首个计分日=状态末日次一交易日
                cred2 = dict(cred)
                cred2.update({"forward_start": str(first_day.date()), "start_nav_90": start_navs[90], "start_nav_120": start_navs[120], "executor_file_hashes_official": fh, "signed_at": pd.Timestamp.now().isoformat(), "scoring": True, "price_basis": "T+1日收盘价模拟成交(非实盘可实现收益口径)"})
                body = json.dumps({k: v for k, v in cred2.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
                cred2["self_hash"] = hashlib.sha1(body.encode()).hexdigest()[:12]
                with open(os.path.join(FWD, "launch_credential_official.json"), "w", encoding="utf-8") as f:
                    json.dump(cred2, f, ensure_ascii=False, indent=1)
                print(f"正式启动凭证已签发: forward_start={cred2['forward_start']} NAV90={start_navs[90]} NAV120={start_navs[120]}")


if __name__ == "__main__":
    main()
