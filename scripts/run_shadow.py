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


def us_calendar(start, end):
    """P0: 独立美股交易日历(工作日-联邦假日, 近似NYSE), 不依赖行情CSV; 带生成时间缓存。"""
    cp = os.path.join(FWD, "calendar_us.json")
    if os.path.exists(cp):
        doc = json.load(open(cp, encoding="utf-8"))
        if doc["start"] == str(start.date()) and doc["end"] == str(end.date()):
            return pd.DatetimeIndex(pd.to_datetime(doc["days"]))
    from pandas.tseries.holiday import USFederalHolidayCalendar
    cal = USFederalHolidayCalendar()
    holidays = cal.holidays(start=start, end=end)
    days = pd.bdate_range(start, end).difference(pd.DatetimeIndex(holidays))
    with open(cp, "w", encoding="utf-8") as f:
        json.dump({"start": str(start.date()), "end": str(end.date()), "generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "days": [str(d.date()) for d in days]}, f)
    return days


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
    """P0阶段一: T日收盘后生成并持久化T+1冻结计划 — T+1来自独立交易日历(不要求T+1行情已存在)。"""
    px_d, ma100_d, ev_d, ownpos_d, snap = mkt["px"], mkt["ma"], mkt["ev"], mkt["own"], mkt["snap"]
    st, done, created, _, pending_plan = _load_run_ctx(hold, state_path, log_path)
    cal = us_calendar(mkt["days"][0] - pd.Timedelta(days=400), pd.Timestamp.now() + pd.Timedelta(days=400))
    pos0 = cal.get_loc(st["last_date"])
    if pos0 + 1 >= len(cal):
        return "calendar_exhausted"
    d = cal[pos0 + 1]  # T+1由日历解析, 无需T+1行情
    if pending_plan is not None and pending_plan["date"] == d:
        return "plan_exists"
    pp = plan_next_day(TICKERS, snap, "V2aF", px_d, ma100_d, ev_d, ownpos_d, st, d, hold_days=hold, calendar=cal)
    if pp["plan_hash"] not in created:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "PLAN_CREATED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "created_at": pd.Timestamp.now(tz="UTC").isoformat(), "plan": _enc(dict(pp))}, default=str) + "\n")
    return "planned"


def settle_phase(mkt, hold, state_path, log_path, allow_replay=False):
    """P0阶段二: T+1收盘数据到达后消费事前计划; created_at晚于成交日→phase_mode=late_created且scoring=false; 计划缺失→PLAN_MISSING。"""
    px_d, ma100_d, ev_d, listed_d, ownpos_d, days_all, snap = mkt["px"], mkt["ma"], mkt["ev"], mkt["listed"], mkt["own"], mkt["days"], mkt["snap"]
    st, done, created, executed_recs, _ = _load_run_ctx(hold, state_path, log_path)
    cal = us_calendar(mkt["days"][0] - pd.Timedelta(days=400), pd.Timestamp.now() + pd.Timedelta(days=400))
    pos0 = cal.get_loc(st["last_date"])
    if pos0 + 1 >= len(cal):
        return "calendar_exhausted"
    d = cal[pos0 + 1]
    if d > days_all[-1]:
        return "data_incomplete"  # P0: T+1收盘数据未到达, 等待
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
        pp = plan_next_day(TICKERS, snap, "V2aF", px_d, ma100_d, ev_d, ownpos_d, st, d, hold_days=hold, calendar=cal)
        if pp["plan_hash"] not in created:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "PLAN_CREATED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "created_at": pd.Timestamp.now(tz="UTC").isoformat(), "mode": "replay", "plan": _enc(dict(pp))}, default=str) + "\n")
        scoring, phase_mode = False, "replay"
    else:
        pp = _dec(plan_rec["plan"])  # P0: 消费事前计划
        body = {k: v for k, v in pp.items() if k != "plan_hash"}
        assert hashlib.sha1(json.dumps(_enc(body), sort_keys=True, default=str).encode()).hexdigest()[:12] == pp["plan_hash"], "事前计划验签失败"
        ca_ts = pd.Timestamp(plan_rec.get("created_at", "1970-01-01"))
        pre = ca_ts.date() < d.date()  # P0: 事前窗口 = 计划生成日严格早于成交日
        scoring, phase_mode = pre, ("forward" if pre else "late_created")
    ca = apply_day(TICKERS, snap, "V2aF", px_d, ma100_d, ev_d, listed_d, ownpos_d, st, pp, d, hold_days=hold, require_config=True)
    st = ca["end_state"]
    if pp["plan_hash"] in done:
        rec = executed_recs[pp["plan_hash"]]
        if state_hash(st) != rec["new_state_hash"] or serialize_fills(ca["fills"]) != rec["fills"]:
            raise AssertionError(f"数据修订冲突: {d} 重算与已登记PLAN_EXECUTED不一致, 拒绝静默恢复")
    else:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "PLAN_EXECUTED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "phase_mode": phase_mode, "scoring": bool(scoring), "data_fingerprint": hashlib.sha1("".join(str(x.date()) for x in days_all).encode()).hexdigest()[:12], "fills": serialize_fills(ca["fills"]), "eq": round(float(ca["eq"].iloc[0]), 6), "fees_cum": round(st["fees"], 4), "new_state_hash": state_hash(st)}, default=str) + "\n")
    save_state(st, state_path)
    return "settled"


def dual_sync_check(p90, p120):
    """P0: 双配置进度一致性(可pytest)。"""
    a, b = load_state(p90)["last_date"], load_state(p120)["last_date"]
    assert a == b, f"双配置进度不一致: 90={a} vs 120={b}, 拒绝运行"
    return a


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    mkt = load_market()
    paths = {hold: (os.path.join(FWD, f"state_{hold}.json"), os.path.join(FWD, f"forward_log_{hold}.jsonl")) for hold in (90, 120)}
    if mode == "status":  # P0: 无参数=只读查看, 不写任何文件
        for hold in (90, 120):
            st = load_state(paths[hold][0])
            exs = [json.loads(l) for l in open(paths[hold][1], encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
            fw = [r for r in exs if r.get("scoring")]
            print(f"HOLD{hold}: 状态末日={st['last_date'].date()} NAV={nav_of(st, mkt['px']):.4f} 已结算={len(exs)} 计分={len(fw)}")
        return
    assert mode in ("plan", "settle", "replay"), "模式必须为plan/settle/replay/status, 禁止自动回放计分"
    dual_sync_check(paths[90][0], paths[120][0])
    data_cutoff = str(mkt["days"][-1].date())
    if mode == "plan":
        print({hold: plan_phase(mkt, hold, *paths[hold]) for hold in (90, 120)})
        return
    if mode == "replay":  # 历史补跑: 永不计分scoring=false, 不签发凭证
        for hold in (90, 120):
            r = settle_phase(mkt, hold, *paths[hold], allow_replay=True)
            print(f"replay[{hold}]: {r}(scoring=false)")
        return
    # settle: 正式路径
    start_navs = {hold: round(nav_of(load_state(paths[hold][0]), mkt["px"]), 6) for hold in (90, 120)}
    assert all(v > 0 for v in start_navs.values()), f"起点NAV无效: {start_navs}"
    results = {hold: settle_phase(mkt, hold, *paths[hold]) for hold in (90, 120)}
    print(f"settle: {results} | 起点NAV(处理前冻结): {start_navs} | 数据截止: {data_cutoff}")
    cred_p = os.path.join(FWD, "launch_credential_v2.json")
    if os.path.exists(cred_p) and results[90] == results[120] == "settled":
        cred = json.load(open(cred_p, encoding="utf-8"))
        fh = {f: hashlib.sha256(open(os.path.join(r"E:\mystockagent", f), "rb").read()).hexdigest()[:16] for f in ("cycle_system/cycle_portfolio.py", "scripts/run_shadow.py")}
        missing = [f for f in fh if f not in cred.get("executor_file_hashes", {})]
        mismatch = [f for f in fh if f in cred.get("executor_file_hashes", {}) and cred["executor_file_hashes"][f] != fh[f]]
        if cred.get("forward_start") == "PENDING_FIRST_REAL_TRADING_DAY":
            assert not mismatch and not missing, f"凭证身份不完整或不符: mismatch={mismatch} missing={missing}, 拒绝签发"  # P0: 缺任一预期身份即拒绝
            # P0: 双配置均须为合格事前计划(forward)且计分日期一致
            for hold in (90, 120):
                exs = [json.loads(l) for l in open(paths[hold][1], encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
                assert exs and exs[-1]["scoring"] and exs[-1]["phase_mode"] == "forward", f"HOLD{hold}最后结算非合格事前计划, 拒绝签发"
            first_day = mkt["days"][list(mkt["days"]).index(load_state(paths[90][0])["last_date"])]
            cred2 = dict(cred)
            cred2.update({"forward_start": str(first_day.date()), "start_nav_90": start_navs[90], "start_nav_120": start_navs[120], "verified_hashes": fh, "signed_at": pd.Timestamp.now(tz="UTC").isoformat(), "scoring": True, "price_basis": "T+1日收盘价模拟成交(非实盘可实现收益口径)", "normalization": "各配置以自身起点NAV归一化为100"})
            body = json.dumps({k: v for k, v in cred2.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
            cred2["self_hash"] = hashlib.sha1(body.encode()).hexdigest()[:12]
            with open(os.path.join(FWD, "launch_credential_official.json"), "w", encoding="utf-8") as f:
                json.dump(cred2, f, ensure_ascii=False, indent=1)
            print(f"正式启动凭证已签发: forward_start={cred2['forward_start']} NAV90={start_navs[90]} NAV120={start_navs[120]}")


if __name__ == "__main__":
    main()
