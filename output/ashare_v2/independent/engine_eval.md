# 双周期引擎分别评估（任务7 · DUAL_CYCLE_V2.R0）

> 复算：`python -X utf8 cycle_system/ashare_v2/tools/engine_eval.py` → 本文数字源自
> `engine_eval.json`（同目录）。**纪律**：只评行为（覆盖/换手/稳定/集中/风险），账本数值为
> 模拟实现读数（含费），**不作任何预期收益/胜率外推**（CALIBRATED=False）；历史窗口
> 2020–2026 已被反复研究，本文全部指标属 REPLAY 证据，不构成样本外验证。

## 证据窗口

| 窗口 | 性质 | 天数 | 说明 |
|---|---|---|---|
| synthetic_run | §9.2 A–H 合成情景 | 988 | 判据可控、rng 函数内播种可重放 |
| shadow_run_realdata | 真实数据增量影子 | 81 | 2026-03 起近期窗口，RESEARCH 模式（降级标注在产物内） |

## 1. 覆盖度（signal_days / 唯一标的 / 记录数）

| 对象 | 合成：信号日率 | 影子：信号日率 | 影子：唯一标的 | 影子：records |
|---|---|---|---|---|
| LONG_CYCLE | 350/988 = **35.4%** | **100%**（行业态常在场） | 176 | 4795 |
| FAST_V | 73/988 = **7.4%** | 86.4% | 131 | 858 |
| MERGED | 397/988 = 40.2% | 100% | 199 | 5384 |

**读法**：Long-Cycle 是"广覆盖低频择时"，Fast-V 是"窄触发高密观察"。合成窗口 FV 仅 3 只标的
触发（SHOCK 判据在 A–H 面板上本就稀缺，符合 §9.2 设计意图：多数标的应 NO_SETUP）。

## 2. 换手与稳定性

| 对象 | 合成 trades/信号日 | 影子 trades | 影子 D+1 候选留存(中位) | LC 状态迁移(影子) | FV 状态分布(影子) |
|---|---|---|---|---|---|
| LONG_CYCLE | 2 | 21 | 0.783 | 全行业合计 3965 | — |
| FAST_V | 5 | 4 | 0.875 | — | NO_SETUP 168 / REVERSAL_FAILED 42 / BOTTOM_SEARCH 20 / SHOCK 10 / CONFIRMED 1 / CANDIDATE… |
| MERGED | 8 | 42 | 0.784 | 3965 | 同上 |

**读法**：FV 候选日际留存更高（同一批超跌股连续在场），但成交远少于 LC（触发到下单需
CONFIRMED+预算+闸门多道）；LC 迁移总量大≠高频交易——行业态逐日演进、持仓"数日至数周"
（§3.1）由测试锁定。影子窗口 FV 成交仅 4 笔：其信号→资金转化受自身 σ/现金优先级影响，
归因见 attribution.csv。

## 3. 集中度

- 资金归因（engine_funding，影子）：LONG_CYCLE 主导（详见 json `funding_by_engine`），MERGED
  口径下同码双引擎只承诺一次资金（§6.2 单承诺，测试锁）。
- 单名：执行后单名下限=1 手、上限=nav×0.10/σ（NAME_RISK_FRAC_OF_NAV 冻结）；合成窗口最大单笔
  名义在 json `max_single_buy_notional`。行业 60% 硬顶含 `__UNKNOWN__` 桶（FV 空行业现受约束）。

## 4. 风险纪律（影子 81 日）

- `exceeded_days`：预算 EXCEEDED 冻结日数见 json；当日全部候选显式拒（RISK_BUDGET_EXCEEDED_FROZEN）。
- `exec_reject_rate`：T+1 执行拒单率（停牌/涨跌停/费后不足）见 json；本轮修复后 E 层撤销/跳过
  一律进 rejects（审查B BUG-6），拒单必达性跨层成立。
- `max_dd_seen`：−0.20 触发线（RISK_BUDGET_DRAWDOWN）未在本窗口击穿（见 json 数值）。

## 5. 三对象小结（不含收益宣称）

| 维度 | Long-Cycle | Fast-V | 合并 |
|---|---|---|---|
| 角色 | 行业周期择时主仓 | 个股恐慌修复补丁 | 单承诺归因+双记录 |
| 覆盖 | 高（行业常在场） | 低触发/高在场观察 | LC 主导 |
| 换手 | 低（数日至数周） | 更低（成交稀少） | — |
| 稳定 | 状态迁移连续、无静默吸收态（修复后 EXPIRED 兜底） | 失败回退可达（修复后 §3.3 复活）、衰竭标志入退出层 | 合并层无丢引擎 |
| 风险 | σ/行业/现金三顶同式 | 同式 + 现在受 UNKNOWN 行业桶约束 | PORT 顶含当日 buys（修复后） |

**已知限制（进未决清单）**：影子窗口仅 81 交易日且为 REPLAY；FV 的 CONFIRMED→REBOUND_ACTIVE
转化在真实窗口观测到 1 例，长期稳定性须待 FORWARD 数据；STRICT 模式当前被四闸门阻断
（strict_gate.json：双引擎候选=0 确认），上述行为均属 RESEARCH 降级口径。
