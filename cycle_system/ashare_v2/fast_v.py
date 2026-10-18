# -*- coding: utf-8 -*-
"""fast_v — 急跌后快速反转引擎 (阶段C实装, 完全独立)。

§3 实现要点:
  - 独立单程时序机, 输入仅价格/量能面板——**不读取 Long-Cycle 状态**(互不为前置, §3.4);
  - 触发全部用"当日收盘可知"量: 回撤/跌幅用收盘对前缀高点与 shift 窗口; 低点不再下移用运行
    最小值的连续计数; 反弹确认 = 收盘对冲击后运行低点(前缀 min)的涨幅——运行 min 在 T 日已知,
    不是未来最低价(§3.2"不得事后用最低价触发");
  - 状态: NO_SETUP/SHOCK_DETECTED/BOTTOM_SEARCH/REVERSAL_CANDIDATE/ENTRY_CONFIRMED/
    REBOUND_ACTIVE/REVERSAL_FAILED/EXPIRED;
  - EXPIRED=修复研究窗自然结束(FV_EXPIRE_D, 仅本引擎; 禁止移植到 Long-Cycle);
  - 当日无行情(停牌近似)→ 不推进成交类状态到 ENTRY/REBOUND(§9.1 停牌安全)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cycle_system.ashare_v2 import config as C
from cycle_system.ashare_v2.schemas import (Availability, DataMode, Engine, Evidence,
                                            FastVState, MembershipTrust, OpportunityRecord)


def _causal_features(s: pd.Series, r: pd.Series, amt: pd.Series) -> dict:
    hi250 = s.rolling(250, min_periods=120).max()
    vol20 = r.rolling(20, min_periods=10).std()
    vol250m = vol20.rolling(250, min_periods=120).mean()
    return {
        "px": s.values, "date_idx": s.index,
        "dd_high": (s / hi250 - 1).values,
        "drop5": (s / s.shift(5) - 1).values,
        "drop10": (s / s.shift(10) - 1).values,
        "drop20": (s / s.shift(20) - 1).values,
        "accel": ((s / s.shift(5) - 1) - (s.shift(5) / s.shift(10) - 1)).values,
        "vol_z": (vol20 / vol250m).values,
        "panic_amt": (amt.rolling(20, min_periods=5).mean() / amt.rolling(250, min_periods=120).mean()).values,
        "ma5": s.rolling(5).mean().values,
        "ma10": s.rolling(10).mean().values,
        "ma20": s.rolling(20).mean().values,
    }


class FastVEngine:
    CANDIDATE_STATES = (FastVState.REVERSAL_CANDIDATE.value, FastVState.ENTRY_CONFIRMED.value)

    def __init__(self, portal, data_mode: str = DataMode.RESEARCH.value):
        self.portal = portal
        self.data_mode = data_mode
        self._feat = {}
        self._states: dict = {}      # code → {day: state}
        self._first_sig = {}
        self._built = False

    def build(self) -> None:
        panel = self.portal.price_panel()
        for code, sub in panel.groupby("code"):
            if len(sub) < 130:
                continue
            sub = sub.set_index("date").sort_index()
            s = sub["close_adj"]
            feats = _causal_features(s, s.pct_change(), sub["amount"])
            feats["has_quote"] = np.isfinite(feats["px"])
            self._feat[code] = feats
            self._run_series(code, feats)
        self._built = True

    def _run_series(self, code: str, f: dict) -> None:
        """单程时序机(只向未来读当日行, 内部量全部前缀可知)。"""
        px = f["px"]
        n = len(px)
        st = FastVState.NO_SETUP.value
        shock_i = None
        run_low = np.nan          # 冲击开始以来的运行最低收盘(前缀 min, T 日已知)
        no_new_low_cnt = 0
        entry_i = None
        rebound_high = np.nan
        states = {}
        for i in range(n):
            p = px[i]
            if not np.isfinite(p):
                states[f["date_idx"][i]] = st
                continue
            if shock_i is None:
                deep = f["dd_high"][i] is not None and np.isfinite(f["dd_high"][i]) and f["dd_high"][i] <= -C.FV_DD_FROM_HIGH
                fast = np.isfinite(f["drop20"][i]) and f["drop20"][i] <= -C.FV_SPEED_DROP_20D
                volx = np.isfinite(f["vol_z"][i]) and f["vol_z"][i] >= C.FV_VOL_Z_ANOMALY
                panic = np.isfinite(f["panic_amt"][i]) and f["panic_amt"][i] >= C.FV_PANIC_VOL_RATIO
                if deep and fast and (volx or panic or np.isfinite(f["accel"][i]) and f["accel"][i] <= -0.03):
                    st = FastVState.SHOCK_DETECTED.value
                    shock_i = i
                    run_low = p
                    no_new_low_cnt = 0
            else:
                age = i - shock_i
                if p < run_low:
                    run_low = p
                    no_new_low_cnt = 0
                else:
                    no_new_low_cnt += 1
                rebound = p / run_low - 1 if run_low and np.isfinite(run_low) else 0.0
                if st == FastVState.SHOCK_DETECTED.value and age >= 3:
                    st = FastVState.BOTTOM_SEARCH.value
                elif st in (FastVState.BOTTOM_SEARCH.value,) and no_new_low_cnt >= C.FV_STABILIZE_D:
                    st = FastVState.REVERSAL_CANDIDATE.value
                elif st == FastVState.REVERSAL_CANDIDATE.value:
                    if p < run_low:                     # 再创新低 → 回到搜索
                        st = FastVState.BOTTOM_SEARCH.value
                    elif rebound >= C.FV_REBOUND_CONFIRM and p > f["ma5"][i] and np.isfinite(f["ma5"][i]):
                        st = FastVState.ENTRY_CONFIRMED.value
                        entry_i = i
                        rebound_high = p
                    elif age > C.FV_EXPIRE_D:
                        st = FastVState.EXPIRED.value
                elif st in (FastVState.ENTRY_CONFIRMED.value, FastVState.REBOUND_ACTIVE.value):
                    rebound_high = max(rebound_high, p)
                    fade = p / rebound_high - 1 if np.isfinite(rebound_high) and rebound_high > 0 else 0.0
                    if p < run_low or fade <= -0.06:
                        st = FastVState.REVERSAL_FAILED.value
                    elif p < run_low * 1.0001 and age > 3:
                        st = FastVState.REVERSAL_FAILED.value
                    elif fade <= -0.03 and p < f["ma10"][i] and np.isfinite(f["ma10"][i]):
                        st = FastVState.REBOUND_ACTIVE.value   # 修复衰竭但未破位: 降档
                    elif (st == FastVState.ENTRY_CONFIRMED.value and i - (entry_i or i) >= 5
                          and np.isfinite(f["ma20"][i]) and p > f["ma20"][i]):
                        st = FastVState.REBOUND_ACTIVE.value
                    if st == FastVState.ENTRY_CONFIRMED.value and i - (entry_i or i) > C.FV_EXPIRE_D:
                        st = FastVState.EXPIRED.value
                elif st in (FastVState.REVERSAL_FAILED.value, FastVState.EXPIRED.value):
                    if age > C.FV_EXPIRE_D + 10:
                        st, shock_i, run_low = FastVState.NO_SETUP.value, None, np.nan
                if age > C.FV_EXPIRE_D and st in (FastVState.SHOCK_DETECTED.value,
                                                  FastVState.BOTTOM_SEARCH.value,
                                                  FastVState.REVERSAL_CANDIDATE.value):
                    st = FastVState.EXPIRED.value
            states[f["date_idx"][i]] = st
        self._states[code] = states

    def state_at(self, code: str, day: str) -> str:
        if not self._built:
            self.build()
        dmap = self._states.get(code, {})
        key = pd.Timestamp(day)
        prior = [k for k in dmap if k <= key]
        return dmap[max(prior)] if prior else FastVState.NO_SETUP.value

    def candidates(self, day: str) -> list:
        """当日 Fast-V 候选——可独立于长期趋势(本方法不查询 Long-Cycle)。"""
        if not self._built:
            self.build()
        out = []
        d = pd.Timestamp(day)
        for code, dmap in self._states.items():
            if d not in dmap:
                continue
            st = dmap[d]
            if st not in self.CANDIDATE_STATES:
                continue
            f = self._feat[code]
            i = f["date_idx"].get_indexer([d])[0]
            if i < 0 or not f["has_quote"][i]:
                continue                                   # 当日无行情 → 不产生候选(停牌安全)
            ev = [Evidence("dd_from_high_250", round(float(f["dd_high"][i]), 4), day, "fast_v_panel", Availability.CONFIRMED.value),
                  Evidence("drop_20d", round(float(f["drop20"][i]), 4), day, "fast_v_panel", Availability.CONFIRMED.value),
                  Evidence("vol_z_20d_vs_250d", None if not np.isfinite(f["vol_z"][i]) else round(float(f["vol_z"][i]), 3),
                           day, "fast_v_panel", Availability.CONFIRMED.value),
                  Evidence("rebound_vs_running_low_prefix_min", round(float(f["px"][i]) / float(np.nanmin(f["px"][max(0, i - 60):i + 1])) - 1, 4),
                           day, "fast_v_panel", Availability.CONFIRMED.value,
                           note="运行最低=前缀min, 当日可知, 非事后最低价")]
            first = day                                   # 每日独立签发(证据asof=当日; 连续可得性由引擎memo诊断)
            self._first_sig.setdefault(code, day)
            rec = OpportunityRecord(
                industry="", industry_taxonomy_version="N/A_STOCK_LEVEL",
                membership_trust=MembershipTrust.UNKNOWN.value,
                code=code, engine=Engine.FAST_V.value, cycle_stage=st,
                key_evidence=ev, first_signal_day=first,
                features_at_signal={"drop5": None if not np.isfinite(f["drop5"][i]) else round(float(f["drop5"][i]), 4),
                                    "drop10": None if not np.isfinite(f["drop10"][i]) else round(float(f["drop10"][i]), 4),
                                    "drop20": round(float(f["drop20"][i]), 4), "accel": None if not np.isfinite(f["accel"][i]) else round(float(f["accel"][i]), 4)},
                entry_kind="REVERSAL_CANDIDATE/ENTRY_CONFIRMED",
                entry_condition="恐慌冲击后企稳(低点不再下移≥3日)+反弹确认(收盘较运行低点≥3%且上穿MA5), 当日收盘可判",
                invalidation_condition="再创新低/自反弹高点衰竭6%修复失败/波动环境改变触发风险失效",
                research_horizon_m=None,
                risk_estimate=None, data_credibility=Availability.CONFIRMED.value,
                decision="NO_TRADE", decision_reason="阶段C仅产出候选; 交易与否由D段统一资金竞争决定")
            errs = rec.validate()
            if errs:
                raise RuntimeError(f"Fast-V机会记录契约违规(不得静默丢弃): {errs} code={code} day={day}")
            out.append(rec)
        return out
