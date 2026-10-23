# DATA_CONFLICT_REPORT — 多源冲突登记（交付物 #5，裁定 C/E）

原则：**冲突登记不覆盖**——任何源间差异进本账，冻结历史数值永不改写（R31 面板、链、账本同理）。

## 1. 已登记冲突案例
| # | 域 | 冲突 | 处置 | 状态 |
|---|---|---|---|---|
| DC-1 | 面板 vs API（2026-10-16） | 000002：R31 面板 close=3.72（sina 快照滞后污染）vs baostock 3.66 | 面板**保留原值不改写**；`CORRECTIONS #3/#9` 锚定；API 10-20 晨自修正=3.66 获证 | CLOSED（证据链完整） |
| DC-2 | sina 当日可用性（10-20） | 15:52 probe 248 no_data vs 10-19 同池 248 ok | 定性=sina **日更滞后 ≥40min**（时间语义非数据冲突）；C 层窗口移 19:30 | OPEN→19:30 复测定稿 |
| DC-3 | baostock 日更延迟（10-20） | crosscheck 5/5 TRADE_DATE_NOT_IN_BAOSTOCK vs sina 同日 probe 也无 | 官方 T+1 日更节奏（10-19 经验次日可取）；非错误 | CLOSED |
| DC-4 | 通道对退市券能力（批1） | sina 结构无源 vs baostock 有能力但通道阻断 | 死券 823 清单待批；并集门定义入 coverage_union | OPEN（03:30 重试） |
| DC-5 | p2110 登记深度不齐（B 线） | 同标准下 600008 首条 2000 vs 600000 首条 2008 | 区间头前一律 UNKNOWN，禁向前延伸（全量后修正：深度好于抽检预估，见 PIT_INDUSTRY §3） | CLOSED |
| DC-6 | p2110 跨标准时序（B 线） | 申万旧(1063 券) vs 证监会2001(992 券)同券变更日期集合重合度 **median=0.0**（n=932，2004 前） | **定性=独立异源事件流（非单标准镜像）**——行业可信度分层依据成立：多标准同期重合=HIGH/单标准=降级/MULTI_DISAGREE 进本账；不择一覆盖 | CLOSED（分层规则入 PIT_INDUSTRY §2 消费侧） |
| DC-7 | sina 双 endpoint 日更不齐（10-20） | 19:32 probe（hq 行情源）248 ok vs crosscheck（daily-hfq 源）5/5 SINA_MISSING_TODAY | 同机构两源更新时点独立，登记不覆盖；对账以 baostock 单日实取（000002 close 3.45）为第三方 = 当日数据真实性获证 | CLOSED |

## 2. 系统性交叉核验计划（未读数，预注册）
1. **批4 = sina raw ↔ R31 冻结面板 ↔ baostock** 三层：同券同日 close 对比，容差 0.5%；超容差进本账新行；R31 值永不改写（差异方向由 baostock 第三方仲裁，如 DC-1 范式）。
2. **adj 体系对账**：sina raw×panel_adj_factor ↔ baostock 后复权——口径桥接验证面板 adj_factor 连续性（恒等式 A/C 已证部分延续使用）。
3. **多标准行业审计**：p2110 六标准（申万旧/新/巨潮/中证/协会）同券同期分类语义一致性——冲突行=行业可信度降级证据，不择一覆盖。

## 3. 冲突对研究读数的影响声明
- 现有全部重放/账本读数基于 R31 面板（含 DC-1 已知污染点，标注体系已覆盖：CORPORATE_ACTION_UNVERIFIED+SW_PARTIAL 等）。
- 冲突**不得**被表述为"数据源谁对谁错"的结论前，缺第三方仲裁；DC 行的 CLOSED 仅指"证据链完整"，非"数值已统一"。

**R31 交叉核验（08:55 实跑）**：六年 356,220 对 close **零冲突**（over_tol=0，p99=0.0）→ DATA_CONFLICT 无新增；files_missing 1/年=R31 券缺某年 sina csv（非价格冲突，登记事实）。既有 DC-1..DC-7 全部维持登记不覆盖原值。
