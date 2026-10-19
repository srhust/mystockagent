# -*- coding: utf-8 -*-
"""exit_manager — 退出管理: 按引擎分套规则 (阶段C实装)。

§5.4/§2.5/§3.3 纪律(测试锁):
  * Long-Cycle: **禁止任何按持有天数的退出**——退出只由状态失效触发:
      INVALIDATED(底部假设被否定)→EXIT; TREND_IMPAIRED(长趋势实质破坏)→EXIT(确认20日已在
      状态机内完成, 本层直接执行); 个股持续显著弱于行业(rel60 连续20日<-10%)→REDUCE,
      <-20%→EXIT; MA20 破位→仅 WARNING, 永不直接 EXIT/REDUCE;
      公司风险(造假/立案)与组合风险预算条款: 数据未接入→DORMANT 显式标注, 不假装存在。
  * Fast-V: 修复失败(REVERSAL_FAILED)→EXIT; 衰竭但未破位(降档 REBOUND_ACTIVE)→WARNING;
      EXPIRED(研究窗自然结束)→EXIT——**引擎研究窗衰减, 非持仓固定天数**, 且该到期判据仅存在于
      Fast-V 路径(Long-Cycle 代码与本模块 LC 分支均无到期天数概念, 自证见 source_discipline_ok)。
分级输出 WARNING / REDUCE(部分减仓比例 REDUCE_FRACTION 工程预设) / EXIT。
"""
from __future__ import annotations

from cycle_system.ashare_v2 import config as C
from cycle_system.ashare_v2.schemas import (Availability, Engine, ExitClass, FastVState,
                                            LongCycleState)

REDUCE_FRACTION = 0.5          # 工程预设(未寻优): 部分减仓一半
LC_WEAK_REDUCE, LC_WEAK_EXIT = -0.10, -0.20


class ExitManager:
    def __init__(self):
        self.dormant_clauses = [
            {"clause": "公司风险(造假/立案/退市风险)", "status": Availability.DATA_BLOCKED.value,
             "note": "公告/监管数据未接入, 不编造; 缺数据期间该条款不生效(显式)"},
            {"clause": "组合风险预算熔断", "status": "IMPL_PHASE_D", "note": "回撤预算由 capital_allocator 风险模块提供"},
        ]

    def evaluate(self, pos, ctx: dict) -> list:
        """pos: VirtualPosition; ctx: {industry_state, fast_v_state, rel_industry_60(近60日个股-行业),
        rel_weak_days, ma20_break, rebound_fade, day} → [(ExitClass, clause, fraction)]"""
        out = []
        if pos.engine == Engine.LONG_CYCLE.value:
            ist = ctx.get("industry_state")
            if ist == LongCycleState.INVALIDATED.value:
                out.append((ExitClass.EXIT.value, "底部假设被否定(INVALIDATED):收盘跌破确认低点", 1.0))
            elif ist == LongCycleState.TREND_IMPAIRED.value:
                out.append((ExitClass.EXIT.value, "趋势实质破坏(TREND_IMPAIRED, 确认期已在状态机完成)", 1.0))
            rel = ctx.get("rel_industry_60")
            if rel is not None:
                if rel <= LC_WEAK_EXIT and ctx.get("rel_weak_days", 0) >= C.LC_IMPAIR_CONFIRM_D:
                    # 缺陷修复(§2.5"持续显著弱于"): -0.20档此前单日即全清, 与-0.10档需60日连续倒挂
                    out.append((ExitClass.EXIT.value,
                                f"个股持续显著弱于行业(rel60={rel:.2f}≤{LC_WEAK_EXIT}, "
                                f"{ctx.get('rel_weak_days')}日≥{C.LC_IMPAIR_CONFIRM_D}确认)", 1.0))
                elif rel <= LC_WEAK_REDUCE and ctx.get("rel_weak_days", 0) >= C.LC_WEAK_VS_INDUSTRY_D:
                    out.append((ExitClass.REDUCE.value, f"个股持续弱于行业且未修复(rel60={rel:.2f}, {ctx.get('rel_weak_days')}日)",
                                REDUCE_FRACTION))
            if ctx.get("ma20_break") and not out:
                out.append((ExitClass.WARNING.value, "MA20破位仅警告(§2.5: 禁止因正常回撤清仓)", 0.0))
        elif pos.engine == Engine.FAST_V.value:
            fst = ctx.get("fast_v_state")
            if fst == FastVState.REVERSAL_FAILED.value:
                out.append((ExitClass.EXIT.value, "修复失败(REVERSAL_FAILED):再创新低或自反弹高点衰竭≥6%", 1.0))
            elif fst == FastVState.EXPIRED.value:
                out.append((ExitClass.EXIT.value, "Fast-V研究窗自然结束(EXPIRED=引擎到期, 非LC固定天数)", 1.0))
            elif fst == FastVState.REBOUND_ACTIVE.value and bool(ctx.get("rebound_fade")):
                # rebound_fade为引擎同源标志(REBOUND_ACTIVE且fade≤-0.03, fast_v.fade_at) — 缺陷修复: 此前全仓无赋值点
                out.append((ExitClass.WARNING.value, "反弹衰竭降档(观察, 未破位)", 0.0))
        else:
            raise ValueError(f"未知引擎标签, 拒绝静默不退出: {pos.engine!r}")
        return out

    def source_discipline_ok(self) -> dict:
        """自证: LC 路径无持有天数退出; FV_EXPIRE_D/held_days 不出现在 Long-Cycle 代码。"""
        import inspect

        import cycle_system.ashare_v2.long_cycle as lc
        src_lc = inspect.getsource(lc)
        src_em = inspect.getsource(__import__("cycle_system.ashare_v2.exit_manager", fromlist=["ExitManager"]))
        lc_region = src_em.split("Engine.FAST_V")[0]
        return {
            "long_cycle_no_held_days": ("held_days" not in src_lc) and ("days_held" not in src_lc)
                                        and ("FV_EXPIRE_D" not in src_lc),
            "lc_exit_branch_no_held_days": ("held_days" not in lc_region) and ("FV_EXPIRE_D" not in lc_region),
        }
