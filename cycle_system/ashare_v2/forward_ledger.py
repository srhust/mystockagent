# -*- coding: utf-8 -*-
"""forward_ledger — 前向SHADOW不可篡改归档链 (独立验收第6项工程件)。

规则:
  * 每行记录 = JSON + {"seq", "prev_hash", "rec_hash"}; rec_hash = sha256(canonical_json(payload)+prev_hash)
    —— 改/删/插任一行 → 其后全部链接断裂(verify 捕获);
  * run_kind 强制标注: 信号日 > 冻结生效交易日 ⇒ FORWARD(前向, 计入未来样本外积累),
    否则 REPLAY(历史回放, 永不与前向混同); 冻结锚缺失时一律 REPLAY(保守);
  * 记录内容含 code_version/config_hash/信号日全量状态/候选/冻结计划(plan_hash)/成交/NAV/拒单——
    由调用方提供, 本模块只做链式完整性与口径守卫, 不生产信号。
"""
from __future__ import annotations

import hashlib
import json
import os

GENESIS = "0" * 64


def _canon(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _rec_hash(payload: dict, prev_hash: str, seq: int) -> str:
    return hashlib.sha256((_canon(payload) + "|" + prev_hash + "|" + str(seq)).encode("utf-8")).hexdigest()


def last_state(path: str) -> tuple:
    """返回 (seq, prev_hash)；文件不存在→(0, GENESIS)。"""
    if not os.path.exists(path):
        return 0, GENESIS
    seq, ph = 0, GENESIS
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            seq, ph = d["seq"], d["rec_hash"]
    return seq, ph


def append_record(path: str, payload: dict, freeze: dict | None = None) -> dict:
    """追加一条链上记录。payload 不需含 seq/hash, 由本函数签发。

    run_kind: 调用方可显式给; 未给则按 freeze.frozen_trade_date 与 payload["signal_day"] 自动判定,
    冻结锚缺失/日期不可比 → REPLAY(保守: 宁可将前向降级为回放, 不可把回放冒充前向)。
    """
    payload = dict(payload)
    if "run_kind" not in payload:
        rk = "REPLAY"
        if freeze and freeze.get("frozen_trade_date") and payload.get("signal_day"):
            if str(payload["signal_day"]) > str(freeze["frozen_trade_date"]):
                rk = "FORWARD"
        payload["run_kind"] = rk
    seq, prev = last_state(path)
    seq += 1
    rh = _rec_hash(payload, prev, seq)
    rec = {"seq": seq, "prev_hash": prev, "rec_hash": rh, **payload}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return rec


def verify(path: str) -> dict:
    """全链校验: 逐行重算 rec_hash + prev_hash 衔接 + seq 连续。返回 {ok, n, broken_at}。"""
    if not os.path.exists(path):
        return {"ok": True, "n": 0, "broken_at": None}
    prev, n, broken = GENESIS, 0, None
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            n += 1
            payload = {k: v for k, v in d.items() if k not in ("seq", "prev_hash", "rec_hash")}
            if d.get("prev_hash") != prev or d.get("seq") != n:
                broken = broken or {"line": i, "reason": "chain_link_or_seq"}
                break
            if _rec_hash(payload, prev, d["seq"]) != d.get("rec_hash"):
                broken = broken or {"line": i, "reason": "payload_or_hash_mismatch"}
                break
            prev = d["rec_hash"]
    return {"ok": broken is None, "n": n, "broken_at": broken}


def read_all(path: str) -> list:
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            out = [json.loads(l) for l in f if l.strip()]
    return out
