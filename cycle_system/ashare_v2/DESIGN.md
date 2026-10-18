# Ashare Dual-Cycle V2 — 架构设计文档 (阶段A交付)

> 依据 `TASK_SPEC.md`(审查者 2026-10-18 批准)。本文档定死目标与验收规则(§11: 开始时一次定死),
> 后续阶段只实现不重新解释。代码版本/配置/数据哈希一律随产出落盘, 不口头引用。

## 0. 定位与红线

- 本系统是**架构升级**, 不是参数优化; 不以提高 2020–2026 回测收益为目标(§13)。
- 旧系统 `cycle_system/swing_m1.py` 保留为可复现对照: 其 `run()` 语义**禁止修改**; 本包只允许
  **只读 import** 复用其无状态函数(如 `load_panel`/`industry_index`/`stock_rank` 的等价设施优先
  走本包 `data_portal`), 任何需要改动旧行为的场景一律用适配层(§6)。
- 冻结不可触碰清单: `output/ashare/swing_m1_B0_full.json`、`swing_m1_B1_full.json`、
  `swing_m1_F2_realloc_full.json`、`manifest.json`(R31 数据锚)、`data/stocks/*.csv`(250)、
  美股 `cycle_system/cycle_portfolio.py` + `scripts/run_shadow.py`(hash 锁定, 由
  `tests/test_cycle_portfolio.py::…380` 机制覆盖, V2 严禁触碰)。
- V2 一切产出写 `output/ashare_v2/`, 每份记录: 代码版本 / 配置 / 数据哈希 / 信息时点(§10)。
- 四态分离声明纪律: **编译通过 ≠ 测试通过 ≠ 远端同步 ≠ 预测有效**。

## 1. 基线与 F2 复现 P0 的处置(§1.2)

- 阶段A 用 `tools/baseline_integrity.py` 重验: 250/250 CSV sha256 ↔ `output/ashare/manifest.json`,
  冻结产物 git-blob 一致性, 工作区脏文件判定。结果落 `output/ashare_v2/baseline_integrity/`。
- F2 复现: 从 git 历史恢复的精确参数集(提交 616fc0d 前既有脚本
  `cycle_system/swing_flow_audit.py` L10)为
  `run("expt", gate=True, exit_mode="monthly", entry_mode="daily", cap=0, alloc_policy="score",
  risk_mode="gate", gate_realloc="realloc")`。
  2026-10-18 11:57 磁盘上出现过一次重跑(F2 产物字节级与 HEAD 一致), 但未留参数记录——本包以
  `tools/f2_repro_check.py` **独立重放**验证, 判定写入 `output/ashare_v2/f2_repro/f2_repro_resolution.json`。
- 若重放一致 → V2 对照基线标注 `BASELINE_REPRODUCED(参数集见文件)`;
  若不一致 → 标注 `BASELINE_UNREPRODUCIBLE`, 冻结数字仅称"历史快照值, 当前不可复现"(§1.2), 
  V2 描述性比较一律以重放产物为对照。两种结果都**不得**改写 `f2_repro_p0.json` 原件(保留历史证据),
  只在其旁边新增解决文档。

## 2. 模块地图与阶段映射

```
schemas.py            枚举/记录契约(机会/状态/PIT/模式)      阶段A实装  ★契约
config.py             预置工程常数(声明未寻优)              阶段A实装  ★契约
data_portal.py        统一数据访问+PIT闸门+快照             阶段B
universe.py           历史股票池(上市/退市/可交易分层)      阶段B
industry_cycle.py     行业分类Provider+阶段判定             阶段B
fast_v.py             Fast-V 状态机/候选/退出               阶段C
long_cycle.py         Long-Cycle 三层信息/状态机/入口/退出  阶段C
signal_merge.py       双引擎候选合并+冲突处理               阶段D
opportunity_ranker.py 可解释机会评分                        阶段D
capital_allocator.py  贪心分配+风险预算+换仓纪律            阶段D
exit_manager.py       WARNING/REDUCE/EXIT 分级(双引擎)      阶段C/D
portfolio.py          双引擎虚拟持仓+真实净目标协调器       阶段E
runner.py             三模式入口(diagnostic/synthetic/shadow) 阶段F
report.py             日频JSON/MD产物+experiment_manifest    阶段F
tools/                阶段A基线工具(baseline_integrity, f2_repro_check)
```

依赖方向: `runner → (long_cycle, fast_v) → industry_cycle/data_portal → universe`;
`signal_merge → opportunity_ranker → capital_allocator → portfolio → ashare_executor(复用)`。
禁止反向依赖; 禁止任何模块 import 后修改 `swing_m1` 的模块级缓存(FACTOR_TABLE 等)。

## 3. Long-Cycle(主引擎, §2)

### 3.1 三层信息(PIT 强制)
- **A 产业基本面适配器**(可插拔, 按行业配置): 猪/铜/金/煤/工程机械/半导体/银行/证券/保险 +
  成长景气(常山北明类, 不套商品周期)。每条记录字段见 DATA_CONTRACT.md §4, 含
  `first_public_ts/effective_ts/fetched_ts`; **策略仅在 `fetched_ts ≤ 信号日收盘` 且
  `effective_ts` 之后才可用**; 缺发布时间 → `UNKNOWN`, 不回填, 该适配器降级为
  `PROXY_ONLY`(研究模式)或触发 `DATA_BLOCKED`(严格模式)。当前外部数据源(能繁母猪/TC-RC/
  净息差等历史发布时点)未接入 → 适配器状态 `DATA_BLOCKED`, 用价格/量能代理时显式标注。
- **B 行业市场表现特征**: 120/250/500 日区间位置、60/120/250 日趋势、6/12/24 月动量、
  相对上证/沪深300 强度、中长期回撤与波动、底部形成/上涨加速/趋势受损结构。全部只用
  T 日及以前窗口; 行业序列一律经 `industry_cycle` 的信任分层(SW_PARTIAL 拼接禁止, §7.2)。
- **C 个股确认**: 盈利与行业周期一致性(受 A 层可得性约束)、相对行业强弱、率先筑底、
  流动性、公司重大风险。

### 3.2 状态机
`DOWNTREND / BOTTOM_WATCH / BOTTOM_FORMING / EARLY_RECOVERY / EXPANSION / TREND_IMPAIRED / INVALIDATED / UNKNOWN`
- 迁移只用当时信息; 禁止用未来最低点标注历史底部(测试 S9.1-3/§2.3)。
- 允许失败回退(BOTTOM_FORMING→DOWNTREND); `INVALIDATED` 需显式失效条件成立(非固定天数)。
- `UNKNOWN` 为数据不足缺省态: 不出信号, 只出诊断。

### 3.3 入场(同一引擎三类入口, §2.4)
`EARLY_REVERSAL / BREAKOUT_CONFIRM / TREND_CONTINUATION`(回撤后趋势仍完好继续参与)。
三者共用一套状态机与评分, 不形成三个互斥短线策略。

### 3.4 退出(§2.5)
禁止固定持仓天数退出。MA20 破位 = `WARNING` 预警, 不等于趋势失效。
EXIT 触发集合: 底部假设被否定 / 中期长期趋势实质破坏 / 个股持续显著弱于行业 / 公司经营风险 /
风险预算触发 / 更优机会满足换仓条件。分级 `WARNING / REDUCE / EXIT`。

## 4. Fast-V(完全独立, §3)

`NO_SETUP / SHOCK_DETECTED / BOTTOM_SEARCH / REVERSAL_CANDIDATE / ENTRY_CONFIRMED /
REBOUND_ACTIVE / REVERSAL_FAILED / EXPIRED`
- 检测特征: 5/10/20 日跌幅与速度、高点回撤、下跌加速度、波动率异常、量能恐慌释放、
  低点不再下移、企稳反弹确认、行业与市场环境、事件风险。核心问题: 快速下跌是否正转变为
  可参与的修复机会。**禁止事后用最低价触发**(REVERSAL_CANDIDATE 只由 T 日收盘可知条件成立)。
- 与 Long-Cycle 互不为前置; 可独立发候选; 不绕过停牌/重大风险/执行底线。
- 退出用反弹失败/修复衰竭/趋势重新转弱/风险失效, 独立于长期退出规则。
- `EXPIRED`: 修复窗口自然结束(研究性时间衰减), 这是引擎自身语义, **不是**Long-Cycle 的
  固定天数退出, 也不用于长期引擎。

## 5. 机会记录与评分(§4)

`OpportunityRecord` 必填字段(schemas.py 契约化):
行业及分类版本 / 股票 / 来源引擎 / 周期阶段 / 关键证据(特征值列表) / 信号首现日 /
当时可用特征快照 / 入场条件 / 失效条件 / 预期研究持有周期 / 风险估计+数据可信度 /
机会评分 / 交易与否决定+理由。
- 评分 = 可解释分项线性式(见 `opportunity_ranker`), 分项权重为**预置工程常数**, 落档声明
  未经样本外校准; 无校准前禁用"预期收益率/胜率"字样; 不得只按 20 日动量排名。

## 6. 统一资金竞争与贪心分配(§5)

- 资金不预分引擎; 无固定持仓数; 当日合格机会统一竞争。
- Greedy 流程: 双引擎全部合格机会 → 统一评分与风险尺度 → 合并同股与高重叠 →
  评估持仓/新机会/现金/相关性 → 按风险调整后**边际**价值排序 → 贪心分配 → 每笔重算剩余
  风险预算 → 无可信正边际即停 → 输出目标持仓与未分配现金。
- 集中度: 允许无机会→全现金 / 单行业 60%+ / 双行业集中 / 多低相关适度分散;
  不继承 30% 单股上限为默认。上限由风险预算与可成交约束决定。
- 风险约束: 行业相关性/共同因子、流动性、波动与尾部、压力情景、T+1、涨跌停不可成交、
  持仓重叠、费用与现金预留、换仓成本。无杠杆; 高分≠无约束满仓。
- 换仓纪律阈值(`config.py`, 事先固定用于工程测试, 不按旧样本收益寻优):
  最小优势差距 / 成本比较 / 原逻辑成立性 / 风险预算变化 / 换手抑制。

## 7. 双引擎协调与执行(§6, 复用优先)

- `portfolio.PortfolioCoordinator`: 每引擎一本虚拟账(持仓+归因+来源理由), 真实组合共享**一个**
  `ashare_executor.Ledger`; 同股双引擎 → 冲突处理器合并目标仓位, **不重复占用资金**;
  卖出订单绑定虚拟头寸 id; 风险强平可覆盖引擎决策; 一般 WARNING 不清除另一引擎持仓;
  卖出优先于依赖其资金的新买入; 同 plan 幂等(SHAW 去重)防重复 SELL/重复扣费。
- 执行层直接复用 `ashare_executor.create_plan/execute_plan/Ledger`(T+1、涨跌停、停牌、整手、
  费用), **不修改**该文件; 若语义不满足 → 适配层 `ashare_v2/executor_adapter.py`(阶段E 视需要)。

## 8. 数据与股票池红线(§7, 细则见 DATA_CONTRACT.md)

- 250 CSV + `manifest.json` 锚定; 历史股票池用 Baostock 名录(R45-48 产物)分层重建;
  **禁止以当前池宣称已消除幸存者偏差**——V2 正式回测资格 = 受 `SURVIVORSHIP_BIAS_PARTIAL` 标注。
- 行业成员信任分层: `SW_PIT_VERIFIED / SW_PARTIAL / CSRC_HISTORICAL_DATE /
  CURRENT_SNAPSHOT_ONLY / UNKNOWN`; 当前实际状态: 申万二级历史成员 = UNKNOWN → 基于行业过滤的
  正式信号在 Strict 模式 **DATA_BLOCKED**; Research 模式允许 `CURRENT_SNAPSHOT_ONLY` 代理并
  逐条标注(这是已知 PIT 缺陷, 显式标注, 不静默)。
- 单日市场诊断(§8)按最近已确认交易日输出; 数据不足显示缺失, 不猜测。

## 9. 验证矩阵(§9/§12 ↔ 测试映射)

| 验收条款 | 测试/证据 | 阶段 |
|---|---|---|
| 1 双状态机真独立 | test_states.py: 并行喂同一行情序列互不影响 | C |
| 2 Long-Cycle 用长周期信息 | 特征面板含120/250/500窗口断言, 禁MA20/60唯一驱动 | C |
| 3 Fast-V 不依赖长期趋势为正 | 合成场景B | C/F |
| 4 贪心无固定行业数/引擎仓位 | 断言 allocator 无该常量 | D |
| 5 高集中/双行业/全空仓 | 合成场景E/F/G | D/F |
| 6 长期退出无固定天数 | 场景C(不因MA20清仓)+D(假设否定必退) | C |
| 7 资金不重复承诺 | 同股双引擎场景+守恒断言 | E |
| 8 T+1/账本/约束 | executor 复用 + 现金守恒/不透支/订单顺序无关测试 | E |
| 9 PIT 不静默绕过 | 数据闸门注入缺陷→报告含标注(场景H) | B/F |
| 10 旧策略零退化 | 阶段A起 pytest 全量基线 + 冻结哈希校验每阶段执行 | A-G |
| 11 单日诊断+影子入口 | runner 三模式冒烟 | F |
| 12 可追溯 | 每产物带 code_version/config/data_hash/asof | A-G |
| 13 完整记录 | G 阶段验收报告含失败项与数据需求 | G |
| 14 不宣称预测有效 | 全部报告口径声明 + grep 检查 | G |

§9.1 工程测试与 §9.2 合成场景 A–H 落 `tests/ashare_v2/`(与模块同阶段交付)。

## 10. 产出文件(§10)与三模式

`runner.py` 子命令: `diagnostic`(只读诊断) / `synthetic`(合成+冻结数据测试) /
`shadow`(收盘计划→次日真实约束模拟, 记录计划/拒单/成交/持仓/现金/NAV/数据快照)。
三模式共享同一核心信号与资金代码路径, 只有执行副作用开关不同。
产物名: `cycle_state_daily.json / fast_v_candidates.json / long_cycle_candidates.json /
opportunity_ranking.json / capital_allocation.json / trade_plans.json / risk_report.json /
daily_cycle_report.md / data_quality_report.json / experiment_manifest.json`, 均落
`output/ashare_v2/`(影子运行再按日期分层)。

## 11. 历史研究纪律(§9.3)

2020–2026 非 OOS。回放/影子产物一律标注 `EXPLORATORY / DESCRIPTIVE`, 禁止参数遍历挑最优;
正式评价(§9.4)仅在数据时点解决+规则冻结后进行, 且"预测有效"只能来自独立未见数据或
冻结后前向影子运行。
