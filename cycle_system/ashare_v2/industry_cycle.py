# -*- coding: utf-8 -*-
"""industry_cycle — 行业成员信任分层 + 时点特征 (阶段B实装)。

信任分层(DATA_CONTRACT §3, 强制不可缺省):
  * 申万历史成员 = SW_PARTIAL —— 5222 条 in_date 记录(ak.index_component_sw), **无 out_date**:
    在册可知起点, 缺席≠非成员; verified 恒 0(R44-48), 永不返回 SW_PIT_VERIFIED(测试锁)。
  * 当前池 sector 列 = CURRENT_SNAPSHOT_ONLY —— 仅代理研究(PROXY_ONLY 标注)。
  * 证监会口径 = CSRC_HISTORICAL_DATE(查询可证时点; V2 不冒充申万)。
特征(§2.2B): 120/250/500 区间位置、60/120/250 趋势、6/12/24 月动量、相对市场强弱、
回撤/波动、底部形成/趋势受损结构——全部 .loc[:day] 前缀计算, PIT 无泄漏(测试复证)。
"""
from __future__ import annotations

import json
import os
from typing import Optional

import pandas as pd

from cycle_system.ashare_v2 import config as C
from cycle_system.ashare_v2.schemas import Availability, LongCycleState, MembershipTrust

_SW_MAP = json.load(open(os.path.join(C.REPO_ROOT, "output", "ashare",
                                      "industry_member_map_v2.json"), encoding="utf-8"))["mapping"]


class IndustryMembershipProvider:
    def sw_records(self, code: str, day: str) -> list:
        """in_date ≤ day 的申万成员记录(起点可证, 退出不可证)。"""
        return [r for r in _SW_MAP.get(code, []) if str(r.get("in_date", "9999")) <= day]

    def sector_of(self, code: str, day: str) -> dict:
        sw = self.sw_records(code, day)
        out = {"code": code, "asof": day,
               "sw_partial": sw if sw else None,
               "sw_trust": MembershipTrust.SW_PARTIAL.value if sw else MembershipTrust.UNKNOWN.value,
               "sw_note": ("in_date可证起点, out缺失(缺席≠非成员)" if sw else
                           "无SW记录: 不能推断非成员——结构性缺口(R44-48 verified=0)")}
        return out


class IndustrySeriesBuilder:
    """行业等权收益指数(快照成分代理)。正式信号在 Strict 下被行业信任闸门拒绝(§7.4)。"""

    def __init__(self, portal):
        self.portal = portal
        self._idx = None
        self._mkt = None

    def _all(self):
        if self._idx is None:
            panel = self.portal.price_panel()
            p = panel[panel["sector"] != ""] if "sector" in panel else panel
            ra = p.pivot(index="date", columns="code", values="close_adj").pct_change()
            sec = p.drop_duplicates("code").set_index("code")["sector"]
            idx = {}
            for sector, codes in sec.groupby(sec).groups.items():
                cols = [c for c in codes if c in ra.columns]
                if cols:
                    idx[sector] = (1 + ra[cols].mean(axis=1).fillna(0)).cumprod()
            self._idx = pd.DataFrame(idx)
            self._idx = self._idx / self._idx.iloc[0]
            self._mkt = (1 + ra.mean(axis=1).fillna(0)).cumprod()
            self._mkt = self._mkt / self._mkt.iloc[0]
        return self._idx, self._mkt

    def index_for(self, industry: str, day: str) -> dict:
        idx, mkt = self._all()
        if industry not in idx.columns:
            return {"industry": industry, "status": Availability.UNKNOWN.value, "series": None}
        s = idx[industry].loc[:day]
        return {"industry": industry, "series": s,
                "status": Availability.PROXY_ONLY.value,
                "trust": MembershipTrust.CURRENT_SNAPSHOT_ONLY.value,
                "market_series": mkt.loc[:day],
                "note": "等权收益指数(与冻结swing_m1同数学口径); 成分=当前快照→PROXY, 正式回测资格受限"}


def _r(v, n=4):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else round(float(v), n)


def stage_features(builder: IndustrySeriesBuilder, industry: str, day: str) -> dict:
    """§2.2B 特征面板——只读 .loc[:day] 前缀(测试证 PIT)。"""
    pack = builder.index_for(industry, day)
    if pack["series"] is None or len(pack["series"]) < 2:
        return {"industry": industry, "asof": day, "status": pack.get("status", Availability.UNKNOWN.value)}
    s = pack["series"]
    m = pack["market_series"]
    px = s.iloc[-1]
    f = {"industry": industry, "asof": day, "status": pack["status"], "px": _r(px)}
    for w in C.LC_RANGE_WINDOWS:
        win = s.iloc[-w:] if len(s) >= w else None
        if win is not None and win.max() > win.min():
            f[f"range_pos_{w}d"] = _r((px - win.min()) / (win.max() - win.min()))
        elif win is None:
            f[f"range_pos_{w}d"] = None
            f.setdefault("_insufficient_history_windows", []).append(w)
        else:                                     # 完全走平: 键存在+原因显式(缺陷修复: 此前键缺失被误标"数据不足")
            f[f"range_pos_{w}d"] = None
            f.setdefault("_flat_window_windows", []).append(w)
    for w in C.LC_TREND_WINDOWS:
        if len(s) > w:
            f[f"trend_{w}d"] = _r(px / s.iloc[-1 - w] - 1)
            f[f"above_ma{w}"] = bool(px >= s.iloc[-w:].mean())
    for mth, d_ in zip(C.LC_MOMENTUM_MONTHS, (126, 252, 504)):
        if len(s) > d_:
            f[f"mom_{mth}m"] = _r(px / s.iloc[-1 - d_] - 1)
    if len(s) >= 121 and len(m) >= 121:
        f["rel_mkt_120d"] = _r((px / s.iloc[-121]) - (m.iloc[-1] / m.iloc[-121]))
        f["rel_mkt_250d"] = _r((px / s.iloc[-251]) - (m.iloc[-1] / m.iloc[-251])) if len(s) >= 251 else None
    w = min(len(s), 500)
    win = s.iloc[-w:]
    f["max_dd_window"] = _r((win / win.cummax() - 1).min())
    f["vol_120d_ann"] = _r(s.pct_change().iloc[-120:].std() * (252 ** 0.5)) if len(s) > 121 else None
    if len(s) >= 120:
        lo_now = s.iloc[-60:].min()
        lo_prev = s.iloc[-120:-60].min()
        f["no_new_low_60d"] = bool(lo_now >= lo_prev * 0.98)
        f["base_rising_120d"] = bool(lo_now > lo_prev)
    if len(s) >= 320:
        ma250_now = s.iloc[-250:].mean()
        ma250_prev = s.iloc[-310:-60].mean()
        f["below_long_trend"] = bool(px < ma250_now)
        f["long_trend_slope_down"] = bool(ma250_now < ma250_prev)
    return f


def classify_state(f: dict) -> str:
    """初版纯函数分类(§2.3 状态语义); C 阶段在其上加带记忆的迁移与失效。缺特征→UNKNOWN。"""
    def g(k):
        return f.get(k)
    if any(f.get(k) is None and not k.startswith("_") and k not in ("status", "industry", "asof", "px")
           for k in ("trend_250d", "range_pos_250d")):
        return LongCycleState.UNKNOWN.value
    up_long = f["trend_250d"] > 0
    above = f.get("above_ma250")
    acc = (f.get("mom_12m") or 0) > 0 and (f.get("trend_60d") or 0) > 0
    impaired = f.get("below_long_trend") and f.get("long_trend_slope_down") and (f.get("trend_250d") or 0) < -0.15
    bottoming = f.get("no_new_low_60d") and f.get("base_rising_120d") and not above and (f.get("range_pos_250d") or 1) < 0.45
    watch = f.get("range_pos_250d") is not None and f["range_pos_250d"] < 0.25 and not bottoming
    if impaired and f["range_pos_250d"] > 0.5 and not above:
        return LongCycleState.TREND_IMPAIRED.value
    if above and up_long and acc:
        return LongCycleState.EXPANSION.value
    if above and (f.get("trend_60d") or 0) > 0:
        return LongCycleState.EARLY_RECOVERY.value
    if bottoming:
        return LongCycleState.BOTTOM_FORMING.value
    if watch:
        return LongCycleState.BOTTOM_WATCH.value
    if (f.get("trend_250d") or 0) < 0 and not above:
        return LongCycleState.DOWNTREND.value
    return LongCycleState.UNKNOWN.value
