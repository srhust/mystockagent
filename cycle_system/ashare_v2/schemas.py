# -*- coding: utf-8 -*-
"""Ashare Dual-Cycle V2 — 记录契约与状态枚举 (阶段A定死)。

设计红线(见 DESIGN.md §3/§4/§8):
  - 双引擎状态机彼此独立, 互不为前置;
  - 每条机会记录必须可解释且带 PIT 标注; 无校准前禁止"预期收益率/胜率"字样;
  - 数据不足显式 UNKNOWN/DATA_BLOCKED, 不静默填缺。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class RunMode(str, Enum):
    DIAGNOSTIC = "DIAGNOSTIC"   # 只读诊断, 不产生真实成交
    SYNTHETIC = "SYNTHETIC"     # 合成/冻结数据工程测试, 不包装为样本外绩效
    SHADOW = "SHADOW"           # 收盘计划→次日按真实约束模拟, 全量记录


class DataMode(str, Enum):
    STRICT = "strict"           # 关键时点缺失→拒绝正式交易信号
    RESEARCH = "research"       # 允许价格代理, 输出 PROXY_ONLY/DATA_LIMITED/UNKNOWN


class LongCycleState(str, Enum):
    """§2.3 — 禁止用未来最低点标注历史底部; UNKNOWN 为数据不足缺省态。"""
    DOWNTREND = "DOWNTREND"
    BOTTOM_WATCH = "BOTTOM_WATCH"
    BOTTOM_FORMING = "BOTTOM_FORMING"
    EARLY_RECOVERY = "EARLY_RECOVERY"
    EXPANSION = "EXPANSION"
    TREND_IMPAIRED = "TREND_IMPAIRED"
    INVALIDATED = "INVALIDATED"
    UNKNOWN = "UNKNOWN"


class FastVState(str, Enum):
    """§3.3 — 独立于 Long-Cycle; REVERSAL_CANDIDATE 只能由当日收盘前可知条件成立。"""
    NO_SETUP = "NO_SETUP"
    SHOCK_DETECTED = "SHOCK_DETECTED"
    BOTTOM_SEARCH = "BOTTOM_SEARCH"
    REVERSAL_CANDIDATE = "REVERSAL_CANDIDATE"
    ENTRY_CONFIRMED = "ENTRY_CONFIRMED"
    REBOUND_ACTIVE = "REBOUND_ACTIVE"
    REVERSAL_FAILED = "REVERSAL_FAILED"
    EXPIRED = "EXPIRED"


class EntryKind(str, Enum):
    """§2.4 — 同一引擎三类入口, 不拆成三个竞争策略。"""
    EARLY_REVERSAL = "EARLY_REVERSAL"
    BREAKOUT_CONFIRM = "BREAKOUT_CONFIRM"
    TREND_CONTINUATION = "TREND_CONTINUATION"


class ExitClass(str, Enum):
    WARNING = "WARNING"     # 预警(如MA20破位), 不等于趋势失效
    REDUCE = "REDUCE"
    EXIT = "EXIT"


class MembershipTrust(str, Enum):
    """§7.2 — 行业分类信任分层, 每条判断必须携带。"""
    SW_PIT_VERIFIED = "SW_PIT_VERIFIED"
    SW_PARTIAL = "SW_PARTIAL"
    CSRC_HISTORICAL_DATE = "CSRC_HISTORICAL_DATE"
    CURRENT_SNAPSHOT_ONLY = "CURRENT_SNAPSHOT_ONLY"
    UNKNOWN = "UNKNOWN"


class Availability(str, Enum):
    """§7.4 — 数据可用性显式标注。"""
    CONFIRMED = "CONFIRMED"
    RECONSTRUCTABLE = "RECONSTRUCTABLE"
    PROXY_ONLY = "PROXY_ONLY"
    DATA_LIMITED = "DATA_LIMITED"
    DATA_BLOCKED = "DATA_BLOCKED"
    PIT_UNVERIFIED = "PIT_UNVERIFIED"
    UNKNOWN = "UNKNOWN"


class Engine(str, Enum):
    LONG_CYCLE = "LONG_CYCLE"
    FAST_V = "FAST_V"


@dataclass
class Evidence:
    """一条可解释证据: 特征值 + 信息时点(全部≤信号日收盘才允许进入评分)。"""
    name: str                    # 特征名(如 range_pos_250d / dd_from_high_20d / pig.margin)
    value: Optional[float]
    asof: str                    # 该值对应的信息日(YYYY-MM-DD)
    source: str                  # 数据集/适配器名
    availability: str            # Availability 枚举值
    note: str = ""


@dataclass
class OpportunityRecord:
    """§4 — 每个机会的强制输出字段(全部不可缺省; 无法获得则相应引擎不得产生该机会)。"""
    industry: str
    industry_taxonomy_version: str          # 如 "SW2021" / "CSRC-2012" / "SECTOR_SNAPSHOT_CURRENT_ONLY"
    membership_trust: str                   # MembershipTrust
    code: str
    engine: str                             # Engine
    cycle_stage: str                        # 对应引擎状态机取值
    key_evidence: List[Evidence] = field(default_factory=list)
    first_signal_day: str = ""              # 信号首现日期(当时口径, 非回溯)
    features_at_signal: Dict[str, Any] = field(default_factory=dict)
    entry_kind: str = ""                    # EntryKind(Fast-V用其自身入口语义)
    entry_condition: str = ""               # 入场条件文本(可复核)
    invalidation_condition: str = ""        # 失效条件文本(禁止"固定N天")
    research_horizon_m: Optional[int] = None  # 预期研究持有周期(月)
    risk_estimate: Optional[float] = None
    data_credibility: str = ""              # Availability
    score: Optional[float] = None
    score_breakdown: Dict[str, float] = field(default_factory=dict)  # 分项→贡献(可解释)
    decision: str = ""                      # TRADE / NO_TRADE
    decision_reason: str = ""
    forbidden_claims_note: str = ("评分未校准, 不构成预期收益率或胜率" )

    def validate(self) -> List[str]:
        errs = []
        req = dict(membership_trust=self.membership_trust, code=self.code, engine=self.engine,
                   first_signal_day=self.first_signal_day, entry_condition=self.entry_condition,
                   invalidation_condition=self.invalidation_condition,
                   data_credibility=self.data_credibility, decision=self.decision)
        for k, v in req.items():
            if not v:
                errs.append(f"缺必填字段: {k}")
        if not self.key_evidence:
            errs.append("key_evidence 不可为空(无可解释证据不得成机会)")
        trust = {e.value for e in MembershipTrust}
        if self.membership_trust not in trust:
            errs.append(f"membership_trust 非法: {self.membership_trust}")
        for e in self.key_evidence:
            if e.asof > self.first_signal_day:
                errs.append(f"PIT违规: 证据 {e.name} 时点 {e.asof} 晚于信号日 {self.first_signal_day}")
        return errs


@dataclass
class VirtualPosition:
    """§6 — 双引擎各自虚拟持仓(归因独立), 同股可有两个虚拟头寸但资金只承诺一次。"""
    position_id: str
    code: str
    engine: str
    shares: int
    opened_day: str
    entry_kind: str
    thesis: str                    # 初始判断(该引擎视角)
    state: str                     # 所属引擎状态机快照
    risk_budget_used: float = 0.0


@dataclass
class NetTarget:
    """PortfolioCoordinator 输出: 真实净目标仓位 + 买卖计划(卖优先于买)。"""
    asof: str
    targets: Dict[str, int] = field(default_factory=dict)          # code → 目标股数(净)
    sells: List[dict] = field(default_factory=list)                # 先执行
    buys: List[dict] = field(default_factory=list)                 # 资金到位后才允许
    released_cash_needed: float = 0.0
    unallocated_cash: float = 0.0
    coverage_note: str = ""        # 同股双引擎合并说明/风险强平覆盖说明


@dataclass
class Provenance:
    """§10 — 每份产出记录代码版本/配置/数据哈希/信息时点。"""
    code_version: str
    branch: str
    config_hash: str
    data_hashes: Dict[str, str]
    asof: str
    generated_at: str
    mode: str
    pit_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"_provenance": asdict(self)}
