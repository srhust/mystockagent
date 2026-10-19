# -*- coding: utf-8 -*-
"""long_cycle — 主引擎: 产业周期反转状态机 (阶段C实装)。

§2 实现要点:
  - 特征全因果滚动(running min/max/shift), 状态在单程时序推进中迁移——**结构上不可能用未来
    最低点标注历史底部**(§2.3); 测试以前缀截断重放恒等复证;
  - 八态: DOWNTREND/BOTTOM_WATCH/BOTTOM_FORMING/EARLY_RECOVERY/EXPANSION/
    TREND_IMPAIRED/INVALIDATED/UNKNOWN, 允许失败回退(BOTTOM_FORMING→DOWNTREND/INVALIDATED);
  - 同一引擎三类入口(§2.4): EARLY_REVERSAL / BREAKOUT_CONFIRM / TREND_CONTINUATION,
    共用状态机, 不是三个竞争策略;
  - 无任何"持有N天退出"分支(§2.1/§2.5); MA20 破位只产生 WARNING(由 exit_manager 分级);
  - Strict 模式: 行业成员信任不足(CURRENT_SNAPSHOT_ONLY/SW_PARTIAL) → 正式候选拒绝,
    输出 DATA_BLOCKED 明细(§7.4), 不静默;
  - A 层基本面适配器: 当前全部 DATA_BLOCKED(外部指标未接入, 不编造) → 机会记录的
    data_credibility 如实降级为 PROXY_ONLY/DATA_LIMITED。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cycle_system.ashare_v2 import config as C
from cycle_system.ashare_v2.schemas import (Availability, DataMode, EntryKind, Engine,
                                            Evidence, LongCycleState, MembershipTrust,
                                            OpportunityRecord)

_ALLOWED_ENTRY_STATES = {LongCycleState.BOTTOM_FORMING.value, LongCycleState.EARLY_RECOVERY.value,
                        LongCycleState.EXPANSION.value}


def industry_feature_table(series: pd.Series, market: pd.Series) -> pd.DataFrame:
    """单行业全时序特征表(全因果)。rp=区间位置, trend/mom=收益, 底部结构=前缀min比较。"""
    f = pd.DataFrame(index=series.index)
    f["px"] = series
    for w in C.LC_RANGE_WINDOWS:
        mx, mn = series.rolling(w, min_periods=w).max(), series.rolling(w, min_periods=w).min()
        f[f"rp{w}"] = (series - mn) / (mx - mn).replace(0, np.nan)
    for w in C.LC_TREND_WINDOWS:
        f[f"trend{w}"] = series / series.shift(w) - 1
    for mth, d_ in zip(C.LC_MOMENTUM_MONTHS, (126, 252, 504)):
        f[f"mom{mth}"] = series / series.shift(d_) - 1
    for w in (20, 60, 120, 250):
        f[f"ma{w}"] = series.rolling(w, min_periods=w).mean()
    f["min60"] = series.rolling(60).min()
    f["dd500"] = series / series.rolling(500, min_periods=250).max() - 1
    r = series.pct_change()
    f["vol120"] = r.rolling(120, min_periods=60).std() * np.sqrt(252)
    lo_now, lo_prev = series.rolling(60).min(), series.shift(60).rolling(60).min()
    f["no_new_low_60"] = lo_now >= lo_prev * 0.98
    f["base_rising"] = lo_now > lo_prev
    f["below_long"] = series < f["ma250"]
    f["slope250_down"] = f["ma250"].diff(60) < 0
    f["rel_mkt_250"] = (series / series.shift(250)) - (market / market.shift(250))
    f["break_ma20"] = series < f["ma20"]          # 只用于 WARNING(§2.5)
    return f


def stock_feature_table(s: pd.Series, amount: pd.Series) -> pd.DataFrame:
    f = pd.DataFrame(index=s.index)
    f["px"] = s
    for w in (5, 10, 20, 60, 120, 250):
        f[f"ma{w}"] = s.rolling(w, min_periods=w).mean()
    f["rp120"] = (s - s.rolling(120).min()) / (s.rolling(120).max() - s.rolling(120).min()).replace(0, np.nan)
    f["trend60"] = s / s.shift(60) - 1
    f["trend250"] = s / s.shift(250) - 1
    f["hi120_prev"] = s.shift(1).rolling(120).max()
    f["amt20"] = amount.rolling(20, min_periods=10).mean()
    lo_now, lo_prev = s.rolling(60).min(), s.shift(60).rolling(60).min()
    f["no_new_low_60"] = lo_now >= lo_prev * 0.98
    f["base_rising"] = lo_now > lo_prev
    return f


class LongCycleEngine:
    def __init__(self, portal, builder, membership, data_mode: str = DataMode.RESEARCH.value):
        self.portal, self.builder, self.membership = portal, builder, membership
        self.data_mode = data_mode
        self._ind_tables: dict = {}
        self._stk_tables: dict = {}
        self._states: dict = {}          # industry → {day: state}
        self._hist: dict = {}            # industry → 状态序列(pd.Series)
        self._first_elig: dict = {}      # (code, entry_kind) → 首现日
        self._built = False
        self._built_upto = None              # 截断build上界(缺陷修复: 越界查询禁止静默陈旧返回)
        self.strict_gate_report: list = []

    def _guard_upto(self, day: str) -> None:
        if self._built_upto is not None and pd.Timestamp(day) > self._built_upto:
            raise RuntimeError(f"截断build(≤{self._built_upto.date()})后查询{day}: 禁止静默返回陈旧态, "
                               "请重新build或查询更早日期(前缀因果守卫)")

    # ── 特征/状态构建(单程时序) ──
    def build(self, until_day: str | None = None) -> None:
        panel = self.portal.price_panel()
        if until_day is not None:
            panel = panel[panel["date"] <= pd.Timestamp(until_day)]
        dates = sorted(panel["date"].unique())
        # 行业层
        industries = sorted({s for s in panel["sector"] if s})
        mkt = None
        for ind in industries:
            pack = self.builder.index_for(ind, str(pd.Timestamp(dates[-1]).date()))
            if pack["series"] is None:
                continue
            if mkt is None:
                mkt = pack["market_series"]
            ft = industry_feature_table(pack["series"], pack["market_series"])
            self._ind_tables[ind] = ft
            self._states[ind] = {}
            prev = None
            mem = {"imp": 0, "ref": None, "above_long": False}
            for day, row in ft.iterrows():
                state = self._step(ind, pd.Timestamp(day).date().isoformat(), row, prev, mem)
                self._states[ind][day] = state
                prev = state
            self._hist[ind] = pd.Series(self._states[ind]).sort_index()
        # 个股层
        g = panel.groupby("code")
        for code, sub in g:
            if len(sub) < 60:
                continue
            sub = sub.set_index("date").sort_index()
            self._stk_tables[code] = stock_feature_table(sub["close_adj"], sub["amount"])
        self._built = True
        self._built_upto = (pd.Timestamp(until_day) if until_day is not None
                            else pd.Timestamp(max(dates)))

    def _step(self, ind, day, f, prev, mem):
        """状态迁移: 只用当日及以前特征行 f(全部因果)。mem: imp=MA250下方连续日,
        ref=底部确认低点, above_long=是否曾收复长期趋势(TREND_IMPAIRED 只对"已收复后再破"成立,
        底部修复期天然在MA250下方, 不得误判受损)。"""
        def ok(k):
            v = f.get(k)
            return v is not None and not (isinstance(v, float) and np.isnan(v))
        def okv(k, default):
            return f[k] if ok(k) else default   # 缺陷修复: `x or d`哨兵对0.0/NaN失效(平坦底部trend60==0被误判)
        if prev is None or not ok("rp250") or not ok("trend250"):
            return LongCycleState.UNKNOWN.value
        below, slope_dn = bool(f["below_long"]), bool(f["slope250_down"])
        mem["imp"] = mem["imp"] + 1 if below else 0
        if ok("ma250") and f["px"] > f["ma250"]:
            mem["above_long"] = True
        impaired = mem["above_long"] and below and mem["imp"] >= C.LC_IMPAIR_CONFIRM_D and slope_dn
        if prev == LongCycleState.UNKNOWN.value:
            prev = LongCycleState.DOWNTREND.value if f["trend250"] < 0 else LongCycleState.EXPANSION.value
        if prev == LongCycleState.INVALIDATED.value:
            if bool(f["no_new_low_60"]):
                mem["ref"] = None
                return LongCycleState.BOTTOM_WATCH.value
            return prev
        if prev == LongCycleState.DOWNTREND.value:
            if f["rp250"] < 0.25 and okv("dd500", 0) < -0.25:
                return LongCycleState.BOTTOM_WATCH.value
            return prev
        if prev == LongCycleState.BOTTOM_WATCH.value:
            if bool(f["no_new_low_60"]) and bool(f["base_rising"]) and f["rp250"] < 0.45 and okv("trend60", -9) > -0.05:
                mem["ref"] = float(f["min60"])
                return LongCycleState.BOTTOM_FORMING.value
            if f["rp250"] > 0.55 and f["trend250"] > 0:
                return LongCycleState.EARLY_RECOVERY.value
            return prev
        if prev == LongCycleState.BOTTOM_FORMING.value:
            if mem["ref"] and f["px"] < mem["ref"] * 0.93:
                mem["ref"] = None
                return LongCycleState.INVALIDATED.value          # 底部假设被否定
            if impaired:
                return LongCycleState.TREND_IMPAIRED.value
            if f["px"] > f["ma60"] and okv("trend60", -9) > 0:
                return LongCycleState.EARLY_RECOVERY.value
            return prev
        if prev == LongCycleState.EARLY_RECOVERY.value:
            if impaired:
                return LongCycleState.TREND_IMPAIRED.value
            if f["px"] > f["ma250"] and f["trend250"] > 0 and okv("mom12", -9) > 0:
                return LongCycleState.EXPANSION.value
            if okv("trend60", 0) < -0.08 and f["rp250"] < 0.35 and mem["ref"]:
                if f["px"] < mem["ref"] * 0.95:
                    mem["ref"] = None
                    return LongCycleState.INVALIDATED.value
                return LongCycleState.DOWNTREND.value              # 失败回退
            return prev
        if prev == LongCycleState.EXPANSION.value:
            if impaired:
                return LongCycleState.TREND_IMPAIRED.value
            if okv("trend60", 0) < 0 and f["px"] < f["ma120"]:
                return LongCycleState.EARLY_RECOVERY.value         # 降档非清仓
            return prev
        if prev == LongCycleState.TREND_IMPAIRED.value:
            if mem["above_long"] and f["px"] > f["ma120"] and okv("trend60", -9) > 0:
                return LongCycleState.EARLY_RECOVERY.value
            if f["trend250"] < -0.15 and f["rp250"] < 0.25:
                return LongCycleState.DOWNTREND.value
            return prev
        return prev

    # ── 查询接口 ──
    def industry_state(self, industry: str, day: str) -> str:
        if not self._built:
            self.build(day)
        self._guard_upto(day)
        d = self._states.get(industry or "", {})
        key = pd.Timestamp(day)
        prior = [k for k in d if k <= key]
        return d[max(prior)] if prior else LongCycleState.UNKNOWN.value

    def _state_evidence(self, ind: str, day: str) -> list:
        ft = self._ind_tables.get(ind)
        if ft is None or pd.Timestamp(day) not in ft.index:
            return []
        row = ft.loc[pd.Timestamp(day)]
        out = []
        for k in ("rp250", "trend250", "mom12", "dd500", "vol120", "rel_mkt_250"):
            v = row.get(k)
            out.append(Evidence(name=f"ind_{k}", value=None if v is None or np.isnan(v) else round(float(v), 4),
                                asof=day, source="industry_cycle(PROXY:snapshot成分)",
                                availability=Availability.PROXY_ONLY.value))
        return out

    def candidates(self, day: str) -> list:
        """当日 Long-Cycle 机会(三类入口)。Strict 模式受行业信任闸门拒绝(显式报告)。"""
        if not self._built:
            self.build(day)
        self._guard_upto(day)
        d = pd.Timestamp(day)
        sample = list(self._stk_tables.keys())[:25]
        tally: dict = {}
        for c in sample:
            t_ = self.membership.sector_of(c, day).get("sw_trust", "UNKNOWN")
            tally[t_] = tally.get(t_, 0) + 1
        trust = max(tally, key=tally.get) if tally else "UNKNOWN"   # 缺陷修复: sector_of("")恒UNKNOWN使理由串误述证据
        if self.data_mode == DataMode.STRICT.value:
            gate = {"day": day, "gate": "industry_membership",
                    "status": Availability.DATA_BLOCKED.value,
                    "reason": f"申万历史成员={trust}(样本{len(sample)}码聚合, SW_PIT_VERIFIED不可得), "
                              "快照成分=CURRENT_SNAPSHOT_ONLY → Strict模式拒绝正式Long-Cycle信号(§7.2/§7.4)"}
            if not any(r["day"] == day and r["gate"] == gate["gate"] for r in self.strict_gate_report):
                self.strict_gate_report.append(gate)     # 缺陷修复: 同日重复调用不再无界append
            return []
        panel = self.portal.price_panel()
        sector_of = panel.drop_duplicates("code").set_index("code")["sector"].to_dict()
        out = []
        for code, st in self._stk_tables.items():
            ind = sector_of.get(code, "")
            if not ind or ind not in self._states:
                continue
            if d not in st.index:
                continue                                  # 当日无行情(停牌近似)→不产生候选(执行底线另在E层)
            ist = self._states[ind].get(d) or self._states[ind].get(
                max([k for k in self._states[ind] if k <= d], default=None), LongCycleState.UNKNOWN.value) \
                if self._states[ind] else LongCycleState.UNKNOWN.value
            if ist not in _ALLOWED_ENTRY_STATES:
                continue
            if d not in self._ind_tables[ind].index:
                continue
            row = st.loc[d]
            if not row.notna().get("px") or not row.notna().get("ma60"):
                continue
            entry = self._entry_kind(ist, row, self._ind_tables[ind].loc[d])
            if entry is None:
                continue
            self._first_elig.setdefault((code, entry), day)   # 机会首现日(诊断用, 不进记录)
            rec = self._record(code, ind, entry, ist, day, row)
            if rec is not None:
                out.append(rec)
        return out

    def _entry_kind(self, istate: str, row: pd.Series, ind_ft_row: dict) -> str | None:
        """三类入口判定(同一引擎; 全当日可知)。"""
        px = float(row["px"])
        try:
            hi120 = row["hi120_prev"]
        except KeyError:
            hi120 = np.nan
        if istate == LongCycleState.BOTTOM_FORMING.value and bool(row["no_new_low_60"]) and bool(row["base_rising"]):
            return EntryKind.EARLY_REVERSAL.value
        if px > ind_ft_row.get("ma60", px + 1) and istate == LongCycleState.EARLY_RECOVERY.value:
            if not np.isnan(hi120) and px >= hi120:
                return EntryKind.BREAKOUT_CONFIRM.value
            if bool(row["no_new_low_60"]):
                return EntryKind.EARLY_REVERSAL.value
        if (istate == LongCycleState.EXPANSION.value and not np.isnan(row["rp120"])
                and 0.25 <= float(row["rp120"]) <= 0.75 and px > row["ma120"]
                and (float(row["trend250"]) > 0 if pd.notna(row["trend250"]) else False)):
            return EntryKind.TREND_CONTINUATION.value
        return None

    def _record(self, code, ind, entry, istate, day, row) -> OpportunityRecord | None:
        """每日记录独立签发: first_signal_day=day(证据asof≤当日=自证); 机会连续可得性由
        _first_elig 在引擎侧诊断, 不写进记录(避免证据asof>首现日的伪违规)。"""
        mem = self.membership.sector_of(code, day)
        cred = Availability.PROXY_ONLY.value
        if entry == EntryKind.TREND_CONTINUATION.value and self._liq_ok(row):
            cred = Availability.DATA_LIMITED.value
        thesis = {EntryKind.EARLY_REVERSAL.value: "行业底部形成+个股率先企稳(前缀可知)",
                  EntryKind.BREAKOUT_CONFIRM.value: "早期复苏中个股突破120日平台高点",
                  EntryKind.TREND_CONTINUATION.value: "扩张期正常回撤后趋势延续(px>MA120且250趋势为正)"}[entry]
        rec = OpportunityRecord(
            industry=ind, industry_taxonomy_version="SECTOR_SNAPSHOT_CURRENT_ONLY",
            membership_trust=(MembershipTrust.SW_PARTIAL.value
                              if mem.get("sw_trust") == MembershipTrust.SW_PARTIAL.value
                              else MembershipTrust.CURRENT_SNAPSHOT_ONLY.value),  # 缺陷修复: 个股级SW分层落到记录(mem此前算而不用)
            code=code, engine=Engine.LONG_CYCLE.value, cycle_stage=istate,
            key_evidence=self._state_evidence(ind, day) + [
                Evidence("stk_rp120", None if np.isnan(row["rp120"]) else round(float(row["rp120"]), 4), day,
                         "stock_table", Availability.CONFIRMED.value),
                Evidence("stk_trend250", None if pd.isna(row["trend250"]) else round(float(row["trend250"]), 4),
                         day, "stock_table", Availability.CONFIRMED.value),
                Evidence("industry_fundamentals", None, day, "adapter-catalog",
                         Availability.DATA_BLOCKED.value, note="A层产业指标未接入,不编造")],
            first_signal_day=day,
            features_at_signal={k: (None if pd.isna(row[k]) else round(float(row[k]), 4))
                                for k in ("rp120", "trend60", "trend250", "ma60", "ma120")},
            entry_kind=entry, entry_condition=thesis,
            invalidation_condition="底部假设被否定(收盘跌破确认低点93%)/趋势实质破坏(MA250下方持续20日且长趋势下行)/个股持续显著弱于行业/风险预算触发",
            research_horizon_m=12 if entry != EntryKind.TREND_CONTINUATION.value else 6,
            risk_estimate=None, data_credibility=cred,
            decision="NO_TRADE", decision_reason="阶段C仅产出候选; 交易与否由D段评分+资金竞争决定")
        errs = rec.validate()
        if errs:
            raise RuntimeError(f"Long-Cycle机会记录契约违规(不得静默丢弃): {errs} code={code} day={day}")
        return rec

    @staticmethod
    def _liq_ok(row) -> bool:
        return not pd.isna(row["amt20"]) and float(row["amt20"]) >= 3e7   # 预设流动性下限(工程常数, 未寻优)
