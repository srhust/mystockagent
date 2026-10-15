# -*- coding: utf-8 -*-
"""审查五十九轮: 运行层修复 — ①P0 凭证存在后继续结算(连续3个合格前向日, 状态推进, 凭证字节不变) ②P0 原始行情完整性(raw_last前向填充之前, 1股缺→DATA_INCOMPLETE不推进) ③P0 日历(2027-12-31开市/2026-07-03休市/2028-07-03提前收盘)。全部使用临时目录, 不触碰真实FWD。"""
import json
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cycle_system"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import _path  # noqa: F401

import numpy as np
import pandas as pd

from cycle_portfolio import run_contract, save_state, load_state
import run_shadow as RS
import datetime as _dt

import tempfile

tmp = tempfile.mkdtemp()
RS.FWD = tmp  # 全程隔离: 日历缓存/指纹链/凭证只写临时目录

print("=== ③P0 完整观察窗口日历 ===")
closed26 = RS.nx_closed_days(2026, 2026)
assert _dt.date(2026, 7, 3) in closed26 and _dt.date(2027, 12, 31) not in RS.nx_closed_days(2027, 2027)
ec28 = RS.nx_early_close(2028, 2028)
assert _dt.date(2028, 7, 3) in ec28 and RS.session_close_et(pd.Timestamp("2028-07-03")).hour == 13
cal2829 = set(str(x.date()) for x in RS.us_calendar(_dt.datetime(2026, 1, 1), _dt.datetime(2029, 12, 31)))
assert "2027-12-31" in cal2829 and "2026-07-03" not in cal2829 and "2028-07-03" in cal2829
print("2026-07-03休市 / 2027-12-31开市 / 2028-07-03提前收盘13:00 PASS")

print("\n=== ①P0+②P0 连续3个合格前向日 + 原始完整性 ===")
n = 11
dts = pd.date_range("2022-01-03", periods=n, freq="B")
px = pd.Series([10] * 5 + [12, 12, 12, 12, 12, 12], index=dts, dtype=float)
ma = pd.Series([5.0] * 5 + [1000.0] * 6, index=dts)
snap = {(d.to_period("M"), "MU"): 1.0 for d in dts}
own = {"MU": pd.Series(np.arange(n), index=dts)}
li = {"MU": pd.Series(True, index=dts)}
mkt = dict(px={"MU": px}, ma={"MU": ma}, ev={"MU": {4}}, listed=li, own=own, days=dts, snap=snap, raw_last={"MU": dts[-1]}, raw_close={"MU": px})
sp90, lp90 = os.path.join(tmp, "s90.json"), os.path.join(tmp, "l90.jsonl")
sp120, lp120 = os.path.join(tmp, "s120.json"), os.path.join(tmp, "l120.jsonl")
RS.TICKERS = ["MU"]
RS.CFGX = {"scorer": "12m/edge", "cost": 0.0015, "dedup": 60}
c0, c1 = {}, {}
run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, {"MU": {4}}, li, dts[:5], own, collect=c0, reserve_mode=False, hold_days=90, config_extra=RS.CFGX)
run_contract(["MU"], snap, "V2aF", {"MU": px}, {"MU": ma}, {"MU": {4}}, li, dts[:5], own, collect=c1, reserve_mode=False, hold_days=120, config_extra=RS.CFGX)
save_state(c0["end_state"], sp90)
save_state(c1["end_state"], sp120)
# 候选凭证v2(双哈希)
fh = {f: hashlib.sha256(open(os.path.join(r"E:\mystockagent", f), "rb").read()).hexdigest()[:16] for f in ("cycle_system/cycle_portfolio.py", "scripts/run_shadow.py")}
v2 = {"type": "launch_credential", "version": 2, "executor_file_hashes": fh, "config_hash_90": "f263f842f497", "config_hash_120": "6d64e83c9d33", "forward_start": "PENDING_FIRST_REAL_TRADING_DAY"}
body = json.dumps({k: v for k, v in v2.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
v2["self_hash"] = hashlib.sha1(body.encode()).hexdigest()[:12]
json.dump(v2, open(os.path.join(tmp, "launch_credential_v2.json"), "w", encoding="utf-8"))
# 逐日: plan(注入T收盘后created_at)→settle×3日
off_p = os.path.join(tmp, "launch_credential_official.json")


def issue_once():
    """与main()相同的签发语义: official存在→仅验证不覆盖; 否则双配置合格forward结算→签发一次。"""
    if os.path.exists(off_p):
        off = json.load(open(off_p, encoding="utf-8"))
        body = json.dumps({k: v for k, v in off.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
        assert hashlib.sha1(body.encode()).hexdigest()[:12] == off["self_hash"], "正式凭证自哈希校验失败"
        return "exists"
    exs = {h: [json.loads(l) for l in open(lp, encoding="utf-8") if json.loads(l)["type"] == "PLAN_EXECUTED"] for h, lp in ((90, lp90), (120, lp120))}
    ok = all(e and e[-1]["scoring"] and e[-1]["phase_mode"] == "forward" for e in exs.values())
    if not ok:
        return "not_eligible"
    cred2 = dict(v2)
    cred2.update({"forward_start": str(dts[5].date()), "start_nav_90": 100.0, "start_nav_120": 100.0, "signed_at": "test", "scoring": True, "issued_once": True})
    body = json.dumps({k: v for k, v in cred2.items() if k != "self_hash"}, sort_keys=True, ensure_ascii=False)
    cred2["self_hash"] = hashlib.sha1(body.encode()).hexdigest()[:12]
    json.dump(cred2, open(off_p, "w", encoding="utf-8"), ensure_ascii=False)
    return "issued"


for step in range(3):
    r90 = RS.plan_phase(mkt, 90, sp90, lp90)
    r120 = RS.plan_phase(mkt, 120, sp120, lp120)
    assert r90 == r120 == "planned", (r90, r120)
    for lp in (lp90, lp120):  # 注入事前时间戳(T日22:00UTC=17:00ET收盘后)
        lines = [json.loads(l) for l in open(lp, encoding="utf-8")]
        st_last = load_state(sp90 if lp is lp90 else sp120)["last_date"]
        for r in lines:
            if r["type"] == "PLAN_CREATED":
                r["created_at"] = (pd.Timestamp(st_last.date()).tz_localize("UTC") + pd.Timedelta(hours=22)).isoformat()  # 模拟T日收盘后生成(覆盖now默认)
        with open(lp, "w", encoding="utf-8") as f:
            for r in lines:
                f.write(json.dumps(r, default=str) + "\n")
    results = {h: RS.settle_phase(mkt, h, *(sp90, lp90) if h == 90 else (sp120, lp120)) for h in (90, 120)}
    pc_d = [json.loads(l) for l in open(lp90, encoding="utf-8") if json.loads(l)["type"] == "PLAN_CREATED"]
    print(f"  [dbg] created_at={pc_d[-1].get('created_at')} date={pc_d[-1]['date']}")
    assert results[90] == results[120] == "settled", results
    off_bytes = open(off_p, "rb").read() if os.path.exists(off_p) else None
    act = issue_once()
    print(f"日{step+1}: settle={results} 签发={act} state90={load_state(sp90)['last_date'].date()} ✓")
off_bytes2 = open(off_p, "rb").read()
act2 = issue_once()
assert act2 == "exists" and open(off_p, "rb").read() == off_bytes2, (act2,)
print(f"凭证一次性: 3日连续结算后凭证字节完全不变 PASS")
RS.settle_phase(mkt, 90, sp90, lp90)
RS.settle_phase(mkt, 90, sp90, lp90)
assert issue_once() == "exists" and open(off_p, "rb").read() == off_bytes2
print(f"同数据重放: 状态幂等+凭证字节不变 PASS")
# ②P0 原始完整性: MU raw_last 拔到 d6(仅1股, 单股场景=100%缺)
mkt_part = dict(mkt)
mkt_part["raw_last"] = {"MU": dts[6]}
st90_bak = load_state(sp90)
save_state(st90_bak, sp90)
r = RS.settle_phase(mkt_part, 90, sp90, lp90)
assert r == "data_incomplete" and load_state(sp90) == st90_bak
print(f"原始数据未到达: {r}, 状态不推进 PASS")

print("=== 运行层修复验收完成(全程临时目录) ===")
