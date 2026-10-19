# Ashare-Stock V2 独立复核总报告（独立验收任务 2/3/8）

分支 `ashare-v2`，冻结锚点 `DUAL_CYCLE_V2.R0`（FREEZE.json：frozen_trade_date=2026-10-16，
HEAD=8857398，30 模块 sha256 + 43 规则常数，config_hash `cc5cb7c990c2a499` 全链唯一变动为
E 期加性常数 `RISK_BUDGET_DRAWDOWN`，预测规则常数零改动）。
复核方法：四路独立只读评审（A 双引擎 / B 资金层 / C 执行适配层 / D C7 逐提交归因），
评审者各自复现脚本实测；父代理集成修复后由全量测试 + 验收串联复验。

## 1. 复核发现与处置总表

### 1.1 评审 A（Long-Cycle / Fast-V / exit_manager / strict_gate）
| 级别 | 发现 | 处置 | 锁定测试 |
|---|---|---|---|
| P0 | fast_v 运行低点先更新后判定 ⇒ "再创新低"恒 False，§3.3 失败回退死分支 | 已修（8857398）：先判后更 | test_fv_new_low_fallback_branch_is_live |
| P0 | REBOUND_ACTIVE/ENTRY_CONFIRMED 非过期（age 可无限，实测 190） | 已修：i−entry_i>FV_EXPIRE_D⇒EXPIRED | test_fv_rebound_active_expires |
| P0 | Fast-V 无 STRICT 闸门（data_mode 存而不用） | 已修：candidates() STRICT 显式阻断+strict_gate_report | test_fv_strict_gate_blocks；strict_gate.json 双引擎 probe=0 |
| P0 | 反弹衰竭（§3.5"反弹衰竭"）无引擎标志、退出层不联动 | 已修：fade_flags/fade_at + runner 注入 + exit WARNING 联动；死 epsilon 分支删除（-0.06⇒REVERSAL_FAILED 复活） | test_fv_rebound_fade_warning_from_engine_flag |
| P1 | `(f[x] or d)` 哨兵把合法 0.0 当缺失（trend60=0 平坦底部不能 FORMING） | 已修：okv() 显式 NaN 判定 | test_lc_flat_bottom_trend60_zero_allows_forming |
| P1 | 截断 build 后越界查询静默陈旧态 | 已修：_built_upto 越界 raise | test_lc_stale_query_beyond_truncation_raises |
| P1 | -0.20 EXIT 单日击穿（§2.5"持续显著弱于"） | 已修：需 rel_weak_days≥LC_IMPAIR_CONFIRM_D 持续 | test_phaseC 阶梯（25日⇒EXIT / 3日⇒不EXIT） |
| P1 | 未知引擎标签静默不退出 | 已修：raise | test_unknown_engine_label_raises_not_silent |
| P1 | strict_gate：strict_blocked() 恒 True / evidence 测试名不存在 / FV 盲区 / status 常假"BLOCKING" | 已修：probe 驱动 + 真实测试名 + FV 纳入 + ASSERTED/BLOCKING 分层 | test_strict_blocked_is_probe_driven |
| P2 | report state_at_day=全历史末日态（PIT 误标）/ 走平窗口缺键 / phaseF 模块级 rng | 已修（前缀态 + _flat_window_windows + rng 入函数） | 全量回归 |
| NOTE | 不修（规则级，入未决清单 §4）：above_long 粘滞、impaired 单日鞭打、UNKNOWN reseed→EXPANSION、freshness 日历天序、SW_PARTIAL 伪 PIT 等 | 见 §4 | — |

### 1.2 评审 B（capital_allocator / PortfolioCoordinator / executor 适配，19 项复现）
| 级别 | 发现（实测） | 处置 |
|---|---|---|
| 高 | BUG-1 swap-in 组合 σ 复验漏当日 buys ⇒ 成交后 σ/NAV=0.2934>0.20（S3） | 已修：risk_now 含 buys，逐笔更新 |
| 高 | BUG-2 swap-in 预算不含自身卖款 ⇒ 资金紧张时批准即撤销、换仓执行化形同虚设（S5） | 已修：avail= cash+est_proceeds+累计 swap 卖款+本笔 out_net |
| 高 | BUG-3 卖→首买间无现金重估 + flat 费高估回款 ⇒ 跳空时 Ledger `现金透支 AssertionError` 崩溃（S4） | 已修：卖 loop 后 _rebudget_pending_buys + est_proceeds 改 ex.fees() 同源（¥5 floor） |
| 中 | BUG-4 record_ids=id() 堆地址不可重放、swap buys 归因空（S6） | 已修：`engine:code:first_signal_day` 确定性键；swap 行 `SWAP:out->in@day` |
| 中 | BUG-5 σ 上限致不足一手误标 INSUFFICIENT_CASH_FOR_ONE_LOT 并触发换仓（S2 自相矛盾证据） | 已修：专属 NAME_RISK_LOT_FLOOR，不触发 swap（§5.4 换仓语义=资金/预算） |
| 中 | BUG-6 E 层撤销/跳过只进 annotations 不进 rejects（S5：候选双消失） | 已修：七路径一律落 plan["rejects"]（SWAP_REVOKED_BUDGET 等新 reason） |
| 低 | 空行业候选 inf 绕过 60% 硬顶 | 已修：`__UNKNOWN__` 集中桶共享硬顶（收紧方向） |
| 低 | 整手三处重复 / 死分支 / holdings 缺 vol20 计零 | 已修：统一 ex.lot_step / 删死分支 / 缺 vol 显式标注 |
| NOTE | risk_stress/PRESSURE_SCENARIOS 未接线（§5.4 压力情景缺实现）；每日 1 笔强卖逐日推进=设计选择 | 入未决清单 §4 |
| 核查 PASS | 贪心守恒/单名σ/行业顶/预算一手可行性 300 例零违例（S8）；EXCEEDED 冻结分支无漏检（S7）；credibility/freshness 无双重折价；swap 判据与 config 同源；措辞纪律零违规 | — |

### 1.3 评审 C（runner/executor 适配、fill 顺序、风险强退）
主要发现（退出优先级仅记录不执行、同码双引擎持仓归因、T+1 窗口）已在验收(4)（44e57df）
处置：`FORCED>EXIT>SWAP>REDUCE` 排序先于 create_plan（成交序=此序）、pos_id 去重、
save/load_state 重建 alloc 桩。残余 NOTE（REDUCE<200 股静默、拆分零股归并偏置）本批已加显式标注。

### 1.4 评审 D — C7=PASS_ANNOTATED 逐提交归因（任务 3）
链 `be2537e→…→44e57df`（现延伸至 8857398/f1a322f）全 9 提交逐 hunk 分类：
- **预测侧 (d) 类改动 = 0**：十文件冻结集合 `git diff 2361082..44e57df` 仅 config +1 加行；
  引擎/数据/评分/权重/阈值自定义点起 diff 为空；config_hash 全链仅变一次（E 加行所致）。
- **C7 漂移归因唯一来源 = 立项前** `5b6d812(R35)`/`c464c51(R36)` 对 swing_m1.py 的只读候选
  审计日志（De Morgan 等价重写+记录字段），类 (b)+(c)，(d)=0；manifest 锚记 R30 磁盘态属
  (a) 陈旧。**执行语义恒等铁证**：F2 重放 `field_diff={}`、18 项冻结指标全等
  （f2_repro_resolution.json，verdict=F2_REPRO_RESOLVED）。
- V2 链自身 6 处 (d) 全在成交/资金执行层（整手取整、卖款重估、预算余量、swap 执行化、
  优先级排序），方向一致为**更保守**，各有提交说明+测试锁。
- **结论：C7 PASS_ANNOTATED 成立且归因完备；冻结策略（基线四文件）执行语义不受影响。**
  纵向对比 D/E 期产物必须标注执行层版本（已在产物 _provenance.code_version）。

## 2. 修复后验证结果

| 项 | 结果 |
|---|---|
| V2 测试 | **87 passed**（含 9 项新增行为锁，此前这些行为"未锁"） |
| 全仓测试 | 135 passed，基线 0 failed |
| acceptance | PASS_WITH_ANNOTATIONS（C7 保留标注；话术扫描 0 违规；F2 RESOLVED） |
| strict_gate 探针 | four_gates_confirmed=true；**LC+FV STRICT 候选双双=0**（FV 盲区已消除） |
| 前向链演练 | plan 2026-10-16 ⇒ run_kind=REPLAY、链 verify ok、broken_at=null |

## 3. 正式前向验证准入判断（任务 8 决策）

**准入的对象与口径**：R0 准入的是"前向影子行为验证"（FORWARD SHADOW），不是交易信号或
正式收益验证。判定分三层：

1. **可准入（本判断即时生效）**：行为验证链路 — 冻结清单（代码+规则常数哈希）、防篡改
   哈希链、REPLAY/FORWARD 严格分离（signal_day>锚日，缺锚=REPLAY）、T 日 15:00–T+1 09:30
   计划窗、冻结执行器只读复用、每日 state/candidates/positions/risk/rejects/NAV 全量归档。
   首个 FORWARD 日 = 2026-10-19（收盘数据入库后 plan；10-20 execute）。
2. **不可准入（维持阻断，非工程缺陷）**：**正式信号/实盘口径**在 STRICT 数据面被四闸门阻断
   （SW 成员 PIT 不可证 / 全市场池缺历史 / 历史 ST 不可判 / 基本面发布时间缺失），修复前
   亦如此——这是数据结构性事实。R0 阶段一切对外口径必须挂 RESEARCH 降级标注。
3. **准入前置的残余风险**（§4 清单中 HIGH 项若在 FORWARD 期触发即停）：压力情景缺实现、
   classify_state 双分类器不一致——已列为 R1 阻断项，FORWARD 期间不得调规则（改动=新
   独立版本+重新冻结）。

**结论**：任务 2/3/5/6 复核—修复—冻结闭环完成，**批准进入前向影子（FORWARD SHADOW）
阶段**；正式信号准入另行以 STRICT 数据缺口关闭证据申请独立评审。

## 4. 未决问题清单（R1 backlog，按严重度；均非本批修复范围）

**HIGH（规则级/R1 必须先决）**
1. `PRESSURE_SCENARIOS`/`risk_stress`/`dormant_clauses`/`LC_MERITMA20_ONLY_WARNING` 全链未接线
   ——§5.4 压力情景退出缺实现（当前只有回撤 20% 单线）。
2. `classify_state`（industry_cycle 表版）与 long_cycle 带记忆状态机双分类器并存；phaseB 同名
   键语义不同 —— 单一事实源待 R1 决策（改哪个=规则变更,需新版本）。
3. SW_PARTIAL 伪 PIT：3890/5222 条 in_date=2021-12-13 单一抓取日、无 out_date；
   `CSRC_HISTORICAL_DATE` 信任层从未被任何代码产出 —— STRICT 四闸门的数据侧根治路径。
4. 全市场历史池缺（G2）→ 幸存者偏差不可消除，SURVIVORSHIP_BIAS_PARTIAL 长期挂标。

**MEDIUM**
5. above_long 粘滞（MA250 上穿后 below_long 退出迟）与 TREND_IMPAIRED 单日鞭打（无持续确认）。
6. WATCH→EARLY_RECOVERY 的 ref=None 分支、UNKNOWN reseed→EXPANSION 跳级。
7. freshness 用日历天且 `_first_elig` 依赖记录顺序；FV 行数≠交易日数（长期停牌失真）。
8. `schemas.validate()` 定义完备但下游未强制（引擎 _record 不自调用）。
9. `_CRED` CONFIRMED=1.0/PROXY=0.35 落差经评分驱动 swap（标签效应）；trades.csv
   engine_attribution 列部分为空（归因主表在 attribution.csv）。
10. record_ids 三元键对同日同码多记录不唯一；JSON 序列化 NaN→非法字面量（历史产物兼容读取需 `float('-inf' in s)` 类防御）。
11. source_discipline_ok 仅字面 grep，不能证明语义合规。

**LOW/保守残余（可解释、方向安全）**
12. swap est_proceeds/ind_after 保守残差（卖出不冲减行业占用→只会错杀不会破顶）。
13. `_split_lots` 余数归份额最大仓（等权时按引擎字母序），措辞"主引擎"已改显式标注。
14. EXCEEDED 每日 1 笔强卖逐日推进（设计选择，深回撤解锁慢）。
15. REDUCE<200 股延迟（已显式标注）；双引擎 100 股不可拆归首引擎（已显式标注）。

## 5. 红线遵守声明

本批全部修改为**工程缺陷修复**，逐条对照 TASK_SPEC 授权文本（§3.3 状态机失败回退、§3.5
反弹衰竭、§2.5 持续显著弱于、§5.4 换仓语义、§6 整手、§12 可追溯）；未修改任何预测阈值/
权重/评分规则；新增测试均为"锁定既有规范行为"而非锁定新规则。修复→冻结→前向的顺序保证
了：2020–2026 历史样本自 R0 起不再用于任何寻优。
