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
    return dict(px=PXd, ma=MA100d, ev=ev_all, listed=LISTd, own=OWNPOS, days=days, snap=SNAP, raw_last={s: closes[s].index[-1] for s in TICKERS}, raw_close={s: closes[s] for s in TICKERS})


def serialize_fills(fills):
    """唯一成交序列化口径: 写盘与恢复比较共用(P0: 避免Timestamp/浮点原值与格式化值不等导致的假冲突)。"""
    return [[s, str(sig), str(fd), act, round(q, 6), round(p, 4), round(fee, 6)] for s, sig, fd, act, q, p, fee in fills]


def _easter(y):
    """匿名格里高利算法: 复活节周日。"""
    import datetime as _dt
    a = y % 19
    b, c = y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo = (h + l - 7 * m + 114) // 31
    da = ((h + l - 7 * m + 114) % 31) + 1
    return _dt.date(y, mo, da)


def nx_closed_days(y0, y1):
    """P0: NYSE/XNYS 规则休市日(非联邦假日近似)。规则: 周六假日→前周五休市; 周日假日→后周一休市。"""
    import datetime as _dt
    closed = set()
    for y in range(y0, y1 + 1):
        E = _easter(y)
        fixed = [_dt.date(y, 6, 19), _dt.date(y, 7, 4), _dt.date(y, 12, 25), E - _dt.timedelta(days=2)]
        ny = _dt.date(y, 1, 1)
        if ny.weekday() == 5:
            pass  # P0: NYSE特例 — 元旦逢周六不安排补休, 前一年12-31正常开市(如2028-01-01周六→2027-12-31开市)
        elif ny.weekday() == 6:
            closed.add(ny + _dt.timedelta(days=1))  # 周日 → 周一休市
        else:
            closed.add(ny)
        for h in fixed:
            wd = h.weekday()
            if wd == 5:  # 周六 → 前周五
                closed.add(h - _dt.timedelta(days=1))
            elif wd == 6:  # 周日 → 后周一
                closed.add(h + _dt.timedelta(days=1))
            else:
                closed.add(h)
        mon1 = lambda m: next(_dt.date(y, m, 1) + _dt.timedelta(days=i) for i in range(7) if (_dt.date(y, m, 1) + _dt.timedelta(days=i)).weekday() == 0)
        closed.add(mon1(1) + _dt.timedelta(weeks=2))  # MLK 3rd Mon
        closed.add(mon1(2) + _dt.timedelta(weeks=2))  # Washington 3rd Mon
        closed.add(max(_dt.date(y, 5, i) for i in range(25, 32) if _dt.date(y, 5, i).weekday() == 0))  # Memorial last Mon
        sep1 = mon1(9)
        closed.add(sep1)  # Labor 1st Mon
        nov1 = _dt.date(y, 11, 1)
        thank = nov1 + _dt.timedelta(days=(3 - nov1.weekday()) % 7 + 21)
        closed.add(thank)  # Thanksgiving 4th Thu
    return closed


def nx_early_close(y0, y1):
    """提前收盘(13:00 ET): 7/3仅当7/4为周二~周四; 12/24仅当12/25为周一~周五(且非补休闭市日); 黑五。"""
    import datetime as _dt
    early = set()
    closed = nx_closed_days(y0 - 1, y1 + 1)
    for y in range(y0, y1 + 1):
        thu = [d for d in (_dt.date(y, 11, i) for i in range(1, 31)) if d.weekday() == 3]
        bf = thu[3] + _dt.timedelta(days=1)  # 4th Thu 的次日
        if bf.weekday() < 5 and bf not in closed:
            early.add(bf)
        j3 = _dt.date(y, 7, 3)
        if j3.weekday() < 5 and _dt.date(y, 7, 4).weekday() in (1, 2, 3) and j3 not in closed:
            early.add(j3)  # 2026-07-04周六→07-03闭市(非提前收盘); 2028-07-04周二→07-03提前收盘
        d24 = _dt.date(y, 12, 24)
        if d24.weekday() < 5 and d24 not in closed:
            early.add(d24)
    return early


def us_calendar(start, end):
    """P0: 独立NYSE/XNYS交易日历(规则休市集), 不依赖行情CSV; 带生成时间缓存。"""
    import datetime as _dt
    cp = os.path.join(FWD, "calendar_nyse.json")
    if os.path.exists(cp):
        doc = json.load(open(cp, encoding="utf-8"))
        if doc["start"] == str(start.date()) and doc["end"] == str(end.date()):
            return pd.DatetimeIndex(pd.to_datetime(doc["days"]))
    closed = nx_closed_days(start.year - 1, end.year + 1)
    days = pd.DatetimeIndex([d for d in pd.bdate_range(start, end) if d.date() not in closed])
    with open(cp, "w", encoding="utf-8") as f:
        json.dump({"start": str(start.date()), "end": str(end.date()), "generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "closed_sample": sorted(str(c) for c in list(closed)[:5]), "days": [str(d.date()) for d in days]}, f)
    return days


def session_close_et(d):
    """P0: T日NYSE收盘时刻(ET, naive): 常规16:00, 提前收盘日13:00。"""
    import datetime as _dt
    ec = nx_early_close(d.year - 1, d.year + 1)
    return _dt.datetime.combine(d.date(), _dt.time(13, 0) if d.date() in ec else _dt.time(16, 0))


def plan_time_valid(created_at, T, Tp1):
    """P0: 事前窗口 Close(T) <= created_at(ET) < Open(T+1)=09:30 ET。"""
    import datetime as _dt
    ts = pd.Timestamp(created_at)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    et = ts.tz_convert("America/New_York").replace(tzinfo=None)
    lo = pd.Timestamp(session_close_et(T))
    hi = pd.Timestamp(_dt.datetime.combine(Tp1.date(), _dt.time(9, 30)))
    return lo <= et < hi


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


def settle_phase(mkt, hold, state_path, log_path, allow_replay=False, dry=False):
    """P0阶段二: T+1收盘数据到达后消费事前计划。dry=True仅完整校验不写盘(原子性: 双配置先dry后commit)。"""
    px_d, ma100_d, ev_d, listed_d, ownpos_d, days_all, snap = mkt["px"], mkt["ma"], mkt["ev"], mkt["listed"], mkt["own"], mkt["days"], mkt["snap"]
    st, done, created, executed_recs, _ = _load_run_ctx(hold, state_path, log_path)
    cal = us_calendar(mkt["days"][0] - pd.Timedelta(days=400), pd.Timestamp.now() + pd.Timedelta(days=400))
    pos0 = cal.get_loc(st["last_date"])
    if pos0 + 1 >= len(cal):
        return "calendar_exhausted"
    d = cal[pos0 + 1]
    if not dry:  # P0: 收盘门禁 — 实际时钟必须晚于成交日NYSE收盘(含提前收盘日)
        close_et = pd.Timestamp(session_close_et(d)).tz_localize("America/New_York").tz_convert("UTC")
        if pd.Timestamp.now(tz="UTC") < close_et.tz_convert("UTC"):
            return "market_not_closed"
    bad_raw = [s for s in TICKERS if d not in mkt["raw_close"][s].index or not np.isfinite(mkt["raw_close"][s].loc[d])]  # P0: 原始记录存在且有效(防中间缺日/NaN被前向填充掩盖)
    if d > days_all[-1] or bad_raw:
        if not dry and bad_raw:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "DATA_INCOMPLETE", "date": d.isoformat(), "missing": bad_raw, "scoring": False}, default=str) + "\n")
        return "data_incomplete"  # T+1收盘数据未到达/部分股票中间缺日或无效价, 等待
    fp = content_fingerprint(mkt, cal[pos0])  # P1: 内容指纹锚定<=T已验收历史; 追加式指纹链(不覆盖)
    fp_p = os.path.join(FWD, "_data_fp.jsonl")
    base = None
    if os.path.exists(fp_p):
        for l in open(fp_p, encoding="utf-8"):
            r = json.loads(l)
            if r.get("asof") and r.get("fp"):  # P1: 全历史链复核 — 任意已验收日的价格修订均可发现(不只基准段)
                if r.get("fp") != content_fingerprint(mkt, pd.Timestamp(r["asof"])):
                    if not dry:  # P0: dry零写盘
                        with open(fp_p, "a", encoding="utf-8") as f:
                            f.write(json.dumps({"type": "DATA_REVISION", "asof": r["asof"], "scoring": False}, default=str) + "\n")
                    raise AssertionError(f"历史行情内容修订: asof={r['asof']}指纹变化, 拒绝计分, 人工裁决")
            if r.get("base"):
                base = r
    if base is None:
        if not dry:
            with open(fp_p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"base": True, "asof": str(cal[pos0].date()), "fp": fp}, default=str) + "\n")
    else:
        if not dry:
            with open(fp_p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"asof": str(cal[pos0].date()), "fp": fp}, default=str) + "\n")
    plan_rec = None
    if os.path.exists(log_path):
        for l in open(log_path, encoding="utf-8"):
            r = json.loads(l)
            if r.get("type") == "PLAN_CREATED" and r.get("date") == d.isoformat():
                plan_rec = r
    if plan_rec is None:
        if not allow_replay:
            if not dry:  # P0: dry零写盘
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
        ca_ts = plan_rec.get("created_at", "1970-01-01")
        T = cal[pos0]  # T = 状态末日 = 计划基准日
        pre = plan_time_valid(ca_ts, T, d)  # P0: Close(T) <= created_at(ET) < Open(T+1)
        scoring, phase_mode = pre, ("forward" if pre else "late_created")
    ca = apply_day(TICKERS, snap, "V2aF", px_d, ma100_d, ev_d, listed_d, ownpos_d, st, pp, d, hold_days=hold, require_config=True)
    st = ca["end_state"]
    if pp["plan_hash"] in done:
        rec = executed_recs[pp["plan_hash"]]
        if state_hash(st) != rec["new_state_hash"] or serialize_fills(ca["fills"]) != rec["fills"]:
            raise AssertionError(f"数据修订冲突: {d} 重算与已登记PLAN_EXECUTED不一致, 拒绝静默恢复")
    else:
        if dry:  # P0: 原子性 — dry仅校验(计划验签/apply/冲突检测全部执行), 不写任何文件
            return "dry_ok"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "PLAN_EXECUTED", "plan_id": pp["plan_hash"], "date": d.isoformat(), "phase_mode": phase_mode, "scoring": bool(scoring), "fills": serialize_fills(ca["fills"]), "eq": round(float(ca["eq"].iloc[0]), 6), "fees_cum": round(st["fees"], 4), "data_fingerprint": fp, "new_state_hash": state_hash(st)}, default=str) + "\n")
    if dry:
        return "dry_ok"
    save_state(st, state_path)
    return "settled"


def content_fingerprint(mkt, upto):
    """P1: 行情内容指纹(日期+各股收盘价), 价格修订可检测(非仅日期序列)。"""
    h = hashlib.sha1()
    px_d = mkt["px"]
    for s in TICKERS:
        ser = px_d[s][px_d[s].index <= upto]
        for dt, v in ser.items():
            h.update(f"{dt.date()}|{s}|{float(v):.6f};".encode())
    return h.hexdigest()[:16]


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
    # P0: 双配置事务恢复 — 上次BOTH_PREPARED未达BOTH_COMMITTED→用冻结计划幂等补齐落后方, 恢复完成前不计新分
    txn_p = os.path.join(FWD, "dual_txn.jsonl")
    txn_recs = [json.loads(l) for l in (open(txn_p, encoding="utf-8") if os.path.exists(txn_p) else [])]
    pend = {}
    for r in txn_recs:
        if r.get("phase") == "BOTH_PREPARED":
            pend[r["date"]] = False
        elif r.get("phase") == "BOTH_COMMITTED":
            pend[r["date"]] = True
    for td, done in pend.items():
        if done:
            continue
        print(f"事务恢复: {td} 未完成BOTH_COMMITTED → 幂等补齐(冻结计划, 不计新分)")
        for hold in (90, 120):
            stx = load_state(paths[hold][0])
            if stx["last_date"].isoformat()[:10] < td:
                rr = settle_phase(mkt, hold, *paths[hold], allow_replay=True)
                assert rr == "settled", f"事务恢复失败 HOLD{hold}: {rr}"
        with open(txn_p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"phase": "BOTH_COMMITTED", "date": td, "recovered": True}, default=str) + "\n")
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
    # settle: 正式路径 — fail-closed: 候选凭证必须存在且身份匹配, 任何写入前校验(P0)
    cred_p = os.path.join(FWD, "launch_credential_v3.json")
    assert os.path.exists(cred_p), "候选凭证缺失: fail-closed拒绝结算(Release Gate)"
    cred0 = json.load(open(cred_p, encoding="utf-8"))
    fh0 = {f: hashlib.sha256(open(os.path.join(r"E:\mystockagent", f), "rb").read()).hexdigest() for f in ("cycle_system/cycle_portfolio.py", "scripts/run_shadow.py")}
    miss0 = [f for f in fh0 if f not in cred0.get("executor_file_hashes", {})]
    mism0 = [f for f in fh0 if f in cred0.get("executor_file_hashes", {}) and cred0["executor_file_hashes"][f] != fh0[f]]
    assert not mism0 and not miss0, f"凭证身份不完整或不符: mismatch={mism0} missing={miss0}, 拒绝结算"
    off_p = os.path.join(FWD, "launch_credential_official.json")
    if os.path.exists(off_p):  # P0: 仅验证并保留 — 不阻断后续每日结算; 策略身份与候选凭证交叉核对
        off = json.load(open(off_p, encoding="utf-8"))
        body = json.dumps({k: v for k, v in off.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
        assert hashlib.sha1(body.encode()).hexdigest()[:12] == off["self_hash"], "正式凭证自哈希校验失败"
        assert off.get("config_hash_90") == cred0.get("config_hash_90") and off.get("config_hash_120") == cred0.get("config_hash_120"), "正式凭证与候选凭证策略身份不一致"
        print(f"正式凭证已存在(forward_start={off['forward_start']}), 自哈希+策略身份校验通过, 继续结算")
    # P1: 计分前预检查 — 双配置事前计划均存在且窗口有效, 否则不写入任何账户
    cal_pre = us_calendar(mkt["days"][0] - pd.Timedelta(days=400), pd.Timestamp.now() + pd.Timedelta(days=400))
    d_next = cal_pre[cal_pre.get_loc(load_state(paths[90][0])["last_date"]) + 1]
    for hold in (90, 120):
        pc = [json.loads(l) for l in open(paths[hold][1], encoding="utf-8") if json.loads(l)["type"] == "PLAN_CREATED" and json.loads(l).get("date", "")[:10] == str(d_next.date())]
        assert pc and plan_time_valid(pc[0]["created_at"], load_state(paths[hold][0])["last_date"], d_next), f"HOLD{hold} 预检查失败: 缺少合格事前计划({d_next.date()}), 拒绝结算"
    start_navs = {hold: round(nav_of(load_state(paths[hold][0]), mkt["px"]), 6) for hold in (90, 120)}
    assert all(v > 0 for v in start_navs.values()), f"起点NAV无效: {start_navs}"
    results_dry = {hold: settle_phase(mkt, hold, *paths[hold], dry=True) for hold in (90, 120)}  # P0: 原子性 — 两配置全部dry校验通过才进入持久化
    assert all(r == "dry_ok" for r in results_dry.values()), f"预校验失败: {results_dry}, 未写入任何账户"
    d_txn = str(cal_pre[cal_pre.get_loc(load_state(paths[90][0])["last_date"]) + 1].date())
    with open(txn_p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"phase": "BOTH_PREPARED", "date": d_txn, "configs": ["90", "120"]}, default=str) + "\n")
    r90 = settle_phase(mkt, 90, *paths[90])
    with open(txn_p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"phase": "HOLD90_COMMITTED", "date": d_txn}, default=str) + "\n")
    r120 = settle_phase(mkt, 120, *paths[120])
    with open(txn_p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"phase": "BOTH_COMMITTED", "date": d_txn}, default=str) + "\n")
    results = {90: r90, 120: r120}
    print(f"settle: {results} | 起点NAV(处理前冻结): {start_navs} | 数据截止: {data_cutoff}")
    if not os.path.exists(off_p) and os.path.exists(cred_p) and results[90] == results[120] == "settled":  # 仅首次签发
        cred = json.load(open(cred_p, encoding="utf-8"))
        fh = {f: hashlib.sha256(open(os.path.join(r"E:\mystockagent", f), "rb").read()).hexdigest() for f in ("cycle_system/cycle_portfolio.py", "scripts/run_shadow.py")}
        if cred.get("forward_start") == "PENDING_FIRST_REAL_TRADING_DAY":
            for hold in (90, 120):
                exs = [json.loads(l) for l in open(paths[hold][1], encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"]
                assert exs and exs[-1]["scoring"] and exs[-1]["phase_mode"] == "forward", f"HOLD{hold}最后结算非合格事前计划, 拒绝签发"
            first_day = mkt["days"][list(mkt["days"]).index(load_state(paths[90][0])["last_date"])]
            cred2 = dict(cred)
            cred2.update({"forward_start": str(first_day.date()), "start_nav_90": start_navs[90], "start_nav_120": start_navs[120], "verified_hashes": fh, "signed_at": pd.Timestamp.now(tz="UTC").isoformat(), "scoring": True, "price_basis": "T+1日收盘价模拟成交(非实盘可实现收益口径)", "normalization": "各配置以自身起点NAV归一化为100", "issued_once": True})
            body = json.dumps({k: v for k, v in cred2.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
            cred2["self_hash"] = hashlib.sha1(body.encode()).hexdigest()[:12]
            with open(off_p, "w", encoding="utf-8") as f:
                json.dump(cred2, f, ensure_ascii=False, indent=1)
            print(f"正式启动凭证已签发(一次性): forward_start={cred2['forward_start']} NAV90={start_navs[90]} NAV120={start_navs[120]}")


if __name__ == "__main__":
    main()
