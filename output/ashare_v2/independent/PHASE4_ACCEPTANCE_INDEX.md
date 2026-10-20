# 第四阶段验收索引（审查者裁定 2026-10-19 20:05 → 证据落点）

状态行：**ENGINEERING_AUDIT_PASS / RESEARCH_REPLAY_ONLY / FORMAL_ADMIT_BLOCKED**（FORMAL_ADMIT.json 不存在，红线维持）。
基线链：18033fc → … → 4724b83（HEAD，均已 push ashare-v2）。

## A — 修正结论
| 条款 | 证据 |
|---|---|
| 7.10pp → PATHWISE_ACCOUNTING_DECOMPOSITION | `independent/AUDIT_FINAL_backtest_credibility.md` §7.2+更正 #3；**附录 D 路径敏感性给出该定性的机理证据**（9.1%期初差→47%终值差，双源不可归因） |
| 26 笔结构负期望 → 样本内负均值不外推 | AUDIT_FINAL §7.3+更正 #4 |
| IP 确认封禁 → SUSPECTED_RATE_LIMIT_OR_CONNECTION_REJECTION | 更正 #5；**精确化后继=更正 #12**（东财 push2his 接口级、非 IP 级、sina/baostock 可达，直连 RemoteDisconnected 取证） |
| RAW 账本 CORPORATE_ACTION_UNVERIFIED | `raw_bt/*/ledger_selfcheck.json` note 字段+R0 报告 F2；**注**：ca_bt 新账本已入账域改标（见 D） |
| 旧污染 jsonl/旧 CSV 证据分离 | `independent/F4_no_cross_run_contamination.json` |

## B — R1 风险恢复状态机（P0）
预注册 `independent/R1_PREREG.md`（R1.0-PREREG 六规则，读数前冻结）；合成测试 6/6；重放 30 迁移/状态日 ACTIVE 1090/COOLDOWN 177/PROBE 252/LOCKOUT 26；确定性逐数（9396.05/1545/18850750.5）；high_water_global min -0.6059 全程未重置（恢复不依赖旧峰值上涨✓）；LOCKOUT→COOLDOWN→PROBE→ACTIVE 强制序✓；R0 对照仅行为叙事（221/1545 LC 供给日交叉证）。产物 `independent/r1_risk_recovery.json`（重建后 parse 校验，更正 #8）。**零参数调优**✓（2020-2026 只验迁移）。

## C — LC 独立审计
`independent/lc_audit.json`：首候选成因/特征可用起点/warm-up；数据预热不足 vs 真实机会缺席区分；三模式同信息边界候选一致性；MA250/500 未动✓；退出层级错配=设计稿不入 R1（`DESIGN_EXIT_LAYER_MISMATCH.md`，裁定 C-3）✓。

## D — 企业行动
可用性：`CA_AVAILABILITY_baostock_2026-10-20.json`（分红/送转 EXISTS、**配股 GAP 诚实登记**、退市边界 6/6、suspend/ST 逐行）；事件库 `full_replay_pool/coverage_report.json`（156 涉事券 100% COVERED、591 事件、UNVERIFIED 0，更正 #11 语义修正记录）；入账：`ca_apply.py`（CAI.0-PREREG 六规则）+7 单测；端到端：`ca_bt/ca_r1_*` 两 run——81d 行为零漂移（38 全 NO_HOLDER）+ multi 窗 **C1/C2/C3/C4 全 n=0 all_ok TRUE**（`ledger_selfcheck --raw`）；旁证：`bonus_factor_corroboration.py` v2 复合公式 21/22（残余 1 入队列）；守恒测试：ca_apply 测试+账本扩展双轨✓。**路径敏感性发现（附录 D）：本域禁一切性能叙事，narrative_ban 已文档化**。

## E — 前向时间+数据通道
run_kind 三值（FORWARD 仅 plan 于 T+1 开盘边界前生成；10-19 补录=LATE；更正 #7）：17 测试；链上首条=seq1 REPLAY(10-16)。**今日 15:52 哨兵**=10-20 盘后 plan 直播（EM 通则首条真 FORWARD；拒则零行闸门如实登记=阻断即设计正确，更正 #12 后新增决策⑤材料 `DESIGN_FORWARD_SOURCE_TRILEMMA.md`，默认 A+C）。更正账本 12 条全 append-only sha256 锚定✓。fetch_day 错误分布/上限/退避/熔断：`fetch_guard.py`（pacing_s 强化，更正 #6 M2 教训）+今日 `sina_channel_probe_2026-10-19.json`（248ok/2format/0network 实测分布）。

## F — 统一验收
四份独立报告 F1-F4 在 `independent/`；R0 未动=F1（4 目录 hash 对比）；R1 可重放=F2 逐数；无跨运行污染=F4；FORWARD/LATE 分离=E-1+今日直播实录（LIVE_2026-10-20.log）；**全套测试 131 passed**；红线审计 R 项全 ok。

## 数据扩展（并行）
POC M1+M2+深化三件套完成；`M3_REVIEW_PACKAGE.md` 待审（全量未启动✓）。

## 悬置待外部时钟/审查者
① 15:52/16:20 哨兵结果；② M3 批准/拒绝；③ 决策⑤ A/B/C；④ A4 行业二源；⑤ 600188 旁证残例；⑥ RIGHTS 配股通道（东财 detail 被封期间不可用，巨潮慢路径候选）。
