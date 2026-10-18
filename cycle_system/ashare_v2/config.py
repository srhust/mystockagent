# -*- coding: utf-8 -*-
"""Ashare Dual-Cycle V2 — 预置工程常数 (阶段A定死)。

纪律声明(TASK_SPEC §5.5/§13): 本文件所有阈值/权重是**事先固定的工程预设**, 仅用于
工程测试与可解释性, **未按 2020-2026 任何样本收益寻优**, 不构成"最优参数"。
修改任何常数须在此文件留注释(谁/何时/为何), 且不得反向声称旧结果因此变好。
"""
from __future__ import annotations

import hashlib
import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ASHARE_V2_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(REPO_ROOT, "data")
STOCKS_DIR = os.path.join(DATA_DIR, "stocks")
OUTPUT_DIR = os.path.join(REPO_ROOT, "output", "ashare_v2")

# ── 冻结锚(只读校验目标, 任何写入=违规) ─────────────────────────────
FROZEN_ARTIFACTS = [
    "output/ashare/swing_m1_B0_full.json",
    "output/ashare/swing_m1_B1_full.json",
    "output/ashare/swing_m1_F2_realloc_full.json",
    "output/ashare/manifest.json",
    "output/ashare/f2_repro_p0.json",
    "output/ashare/data_audit.json",
    "output/ashare/swing_flow_audit.json",
    "output/ashare/swing_dd_attribution_v2.json",
]
US_FROZEN_FILES = [  # 美股HOLD 90/120 hash锁定, V2严禁触碰(连import副作用都避免)
    "cycle_system/cycle_portfolio.py",
    "scripts/run_shadow.py",
]
# F2 冻结账本数字(commit 616fc0d 记录 + swing_m1_F2_realloc_full.json)
F2_LEDGER = {"return": 0.4964, "ann": 0.0612, "max_dd": -0.3515, "calmar": 0.17,
             "sharpe": 0.38, "buy": 163, "sell": 154, "s2gate": 1253}
# 从git历史恢复的F2精确参数集(cycle_system/swing_flow_audit.py L10, R31入库)
F2_REPRO_PARAMS = dict(strategy="expt", gate=True, exit_mode="monthly", entry_mode="daily",
                       cap=0, alloc_policy="score", risk_mode="gate", gate_realloc="realloc")

# ── Long-Cycle 窗口(§2.2B/§2.1 研究窗口 12/24/36 个月) ──────────────
LC_RANGE_WINDOWS = (120, 250, 500)      # 价格区间位置(交易日)
LC_TREND_WINDOWS = (60, 120, 250)       # 趋势窗口
LC_MOMENTUM_MONTHS = (6, 12, 24)        # 月动量
LC_RESEARCH_WINDOWS_M = (12, 24, 36)    # 研究窗口(月)——持仓数月至一年以上, 无固定天数卖出
LC_BOTTOM_LOOKBACK_D = 250              # 底部形成结构回看(交易日)
LC_IMPAIR_CONFIRM_D = 20                # 趋势"实质破坏"确认所需持续交易日(前向窗口内可知的连续计数)
LC_WEAK_VS_INDUSTRY_D = 60              # "持续显著弱于行业"判定窗口
LC_MERITMA20_ONLY_WARNING = True        # MA20破位仅WARNING(§2.5), 不允许单独成为EXIT理由

# ── Fast-V 检测(§3.2, 预设未寻优) ───────────────────────────────────
FV_SHOCK_WINDOWS_D = (5, 10, 20)        # 急跌观察窗口
FV_DD_FROM_HIGH = 0.18                  # 高点回撤阈值(样本内常见恐慌幅度量级, 预设)
FV_SPEED_DROP_20D = 0.15                # 20日跌幅阈值
FV_VOL_Z_ANOMALY = 1.5                  # 波动率异常 z 阈值(250日基线)
FV_PANIC_VOL_RATIO = 1.8                # 恐慌量能: 20日均量比阈值
FV_STABILIZE_D = 3                      # 低点不再下移连续计数
FV_REBOUND_CONFIRM = 0.03               # 企稳反弹确认幅度(收盘口径)
FV_EXPIRE_D = 40                        # 修复研究窗口上限(交易日)——仅Fast-V语义(EXPIRED),
                                        # 禁止移植到Long-Cycle(§2.1)
# ── 统一资金竞争与风险(§5) ──────────────────────────────────────────
NAV0 = 20000.0                          # 与旧执行器账本同基(仅影子/合成的起始口径, 非绩效宣称)
ENGINE_SPLIT_FRAC = None                # 恒为None: 禁止固定引擎仓位(§5.1; 测试直接断言此常量)
MAX_POSITIONS = None                    # 恒为None: 不固定持仓数量(§5.1)
INDUSTRY_CONCENTRATION_ALLOWED = 0.60   # 允许研究单行业60%+的**上限上限**, 实际由风险预算决定
CASH_RESERVE_FRAC = 0.05                # 现金预留(费用/流动性)
NAME_RISK_FRAC_OF_NAV = 0.10            # 单名风险预算: 该仓位对NAV的"日波动"贡献上限(由σ折算, 非固定市值占比)
PORT_DAILY_RISK_FRAC = 0.20             # 组合层日风险预算(σ口径)占NAV上限, 超出→停止贪心
SWAP_MIN_SCORE_EDGE = 0.5               # 换仓最小优势差距(评分单位, 预设)
SWAP_COST_MULT = 2.0                    # 换仓需覆盖 预估费用×倍数(预设)
SWAP_DAILY_LIMIT = 2                    # 换手抑制: 单日换仓笔数上限(预设)
PRESSURE_SCENARIOS = {"index_down_8pct_5d": 0.08, "vol_x2": 2.0}  # 压力情景(§5.4)

# ── 评分分项权重(§4/§5.3: 可解释线性式, 预设未校准) ──────────────────
SCORE_WEIGHTS = {
    "cycle_stage_quality": 0.30,        # 周期阶段位置(底部形成/早期复苏权重最高, 已扩张递减)
    "long_horizon_evidence": 0.20,      # 长窗口证据(区间位置/长趋势/月动量一致性)
    "relative_strength": 0.15,          # 相对行业/市场强弱
    "liquidity_executability": 0.15,    # 流动性与可成交性(含停牌/涨跌停风险)
    "data_credibility": 0.20,           # 数据可信度(PIT标注越低越折价——§5.3"无可信正边际即停")
}
CALIBRATED = False                      # 无校准前禁称"预期收益率/胜率"(§4); 测试检查措辞

# ── PIT 行为(§7.4) ──────────────────────────────────────────────────
PIT_STRICT = "strict"
PIT_RESEARCH = "research"
DEFAULT_DATA_MODE = PIT_STRICT          # 正式信号默认严格; 研究诊断显式切换

# ── 产出文件(§10) ───────────────────────────────────────────────────
OUTPUT_FILES = ["cycle_state_daily.json", "fast_v_candidates.json", "long_cycle_candidates.json",
                "opportunity_ranking.json", "capital_allocation.json", "trade_plans.json",
                "risk_report.json", "daily_cycle_report.md", "data_quality_report.json",
                "experiment_manifest.json"]


def config_hash() -> str:
    """本模块全部大写常数的规范化哈希(随产出落盘, 供追溯)。"""
    body = {k: v for k, v in sorted(globals().items()) if k.isupper() and isinstance(v, (int, float, str, bool, tuple, list, dict, type(None)))}
    return hashlib.sha256(repr(sorted((k, repr(v)) for k, v in body.items())).encode()).hexdigest()[:16]
