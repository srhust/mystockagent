# Ashare-Stock V2 第二轮复核处置报告 (ROUND-2 DISPOSITION)

日期: 2026-10-19 · 提交: d70f1d8 (验收9) + 本次增量 · 分支: ashare-v2 · 裁定基线: 第二轮复核(基础架构 PASS / 计划链受限演练 / 自动成交暂缓 / 预测有效性 NOT_CLAIMED)

红线遵守声明: 本批全部改动仅涉**前向执行与审计正确性**; Fast-V、Long-Cycle、机会评分、风险阈值、资金竞争参数**零改动**——机器证据见 §1。

## 1. 预测规则零变化证明 (git)

```
git diff --stat 8857398..HEAD -- fast_v.py long_cycle.py exit_manager.py opportunity_ranker.py \
  capital_allocator.py config.py signal_merge.py schemas.py industry_cycle.py universe.py \
  data_portal.py ashare_executor.py
→ (空输出: 12个预测/规则/冻结执行文件自R0冻结点逐字节未变)
```
- 规则常数: `FREEZE_ADDENDUM_R0.1` 机器比对 `param_diff = {}` (43 常数, tuple/list 归一后)。
- config_hash 保持 `cc5cb7c990c2a499`。
- B0/B1/F2: baseline_integrity C1–C7 verdict PASS (C7=PASS_ANNOTATED, 冻结文件本体零修改, 注解=第二轮P0修复记录); F2 复现 `field_diff_keys=[]` → F2_REPRO_RESOLVED 不退化。
- 变动文件全集 = portfolio.py (T+1调整记录语义) + 4个工具/入口 + 测试 + 审计产物; 均非预测规则。

## 2. 三 P0 修复

| P0 | 缺陷 | 修复 | 负例测试锁定 |
|---|---|---|---|
| P0-1 执行价 | cmd_execute 用 T+1 `close_adj` 充当 09:40 成交价(未来信息+口径错) | `execution_mode=DELAYED_REBUILD_RAW_OPEN`: 收盘后用增量层当日 **raw open**(09:25 集合竞价后即固定, 无时间穿越)延迟重建; 涨跌停/费用/金额全 raw 域; 当日 15:00 前运行直接拒绝 rc=5; **不声称实时执行**, 记录=重建审计 | 成交价逐笔=当日open≠close; 盘中拒绝 |
| P0-2 数据分层 | 新交易日追加进冻结 R31 CSV → 哈希失配 → 前向任务被数据完整性检查杀死 | 新 `forward_ingest`: 冻结层永不改写(前向期禁跑 pipeline 全量); 增量层 `forward/incremental/<day>.csv` + `meta{fetched_at,source,sha256,codes_missing,close_trading_after_fetch}`; plan 要求收盘后抓取证明; merge 时增量覆盖冻结日期直接 raise; 校验零静默关闭(冻结层仍 verify_hashes=True) | 缺日 rc=6 结构化拒绝; meta哈希失配 raise; 覆盖冻结日 raise; 盘中抓取禁 plan |
| P0-3 计划不可变 | T+1 `_rebudget_pending_buys` 用新 plan 哈希+伪造 created_at 替换 T 日冻结计划 | 只许**缩减或撤销**(验收14补全CANCEL: 现金不足最小申报单位→event=CANCEL+父哈希+budget_after=0, 不建零预算假计划, 执行留cancelled_T+1_min_lot拒单; REDUCE/CANCEL均带event标签); 每笔生成 `{parent_plan_hash, child_plan_hash, budget_before/after, monotone_down, code_unchanged, wall_clock, reason}` 入 adjust_log 并持久化; T 日原 plan 快照哈希留档链上; 换码/增股不可能(仅缩cash_budget) | 单调缩减+父链断言; 无二次调整 |

## 3. 两条纪律

1. **FORWARD 记录时间证明**: 每条 PLAN/EXECUTE 带 `wall_clock`(记录生成真实时刻)+`data_fetched_at`(行情可见时刻)+`data_csv_sha256`+`module_audit`(16个预测模块与 R0∪Addendum 基线**逐64位哈希**比对, 失配=拒绝FORWARD记账 raise; 辅助工具变更单独落 `tool_changes` 不冒充预测); `code_version`+`config_hash` 随 `_prov()`。R0 原件保留, 执行工具版本经 `FREEZE_ADDENDUM_R0.1`(modules_changed=6, 规则常数零变化)追加而非覆盖。
2. **幂等/恢复/链头**: 同日重复 plan、重复/无计划 execute、链失序 → 全部结构化拒绝(rc=3/2/4), 实测 live: 对已存在的 10-16 PLAN 再次 plan → `IDEMPOTENT_PLAN`。独立链头存档 `chain_head.json`(seq+尾哈希+时刻), `seal` 子命令封存既有链; verify 做**头哈希逐字节比对**→ 整链重写(重算哈希自洽)也被发现(负例测试: 篡改 nav 后重建链, verify 非零退出)。

## 4. 正式账本暂停与准入 (裁定执行)

- 所有新记录默认 `formal_gate=DIAGNOSTIC_REHEARSAL`; `FORMAL` 需 `--formal` **且** `FORMAL_ADMIT.json` 准入文件存在。该文件**未创建**——待本批修复经复核者验证 + 完整 T/T+1 实盘记录产出后重走前向准入再开。
- 只读诊断与 plan 演练保留(verify/status 任意跑)。

## 5. 定时任务实测 (指令7)

| 任务 | cron(Asia/Shanghai) | 语义 | 验证 |
|---|---|---|---|
| schedule-842e6487 | `40 15 * * 1-5` | T日: `fetch <day>`(收盘后增量抓取, 禁 pipeline) → `plan <day>`; 要求确认 run_kind/formal_gate/chain_seq/head_hash; 拒绝码处置规则写入 prompt | cron 表达式+zone 回读 OK; scheduledAt=2026-10-19 15:40+08 |
| schedule-e1be29c8 | `10 16 * * 1-5` (原09:40作废) | T+1: `fetch <day>` → `execute <day>` raw-open 延迟重建; 非15:00后拒绝在代码内(与时刻表双保险) | 回读 OK; 16:10>15:00 满足收盘后约束 |
| 依赖 | python(anaconda)+pandas+akshare | 工作目录=仓库根(工具自带 _path 引导, 任意 cwd 可跑) | akshare 实测: fetch_one 600010 raw+hfq 双口径返回至 2026-10-16 ✓; 增量全池抓取 2026-10-19 实测运行中(见 §6) |
| 退出码 | plan 0/2/3/4/6/7; execute 0/2/3/5/6/7; verify 0/1; seal 0/1 | 全部结构化 JSON stdout, 无裸 traceback | 负例测试覆盖(含rc7双防线) |

## 4.1 正式绩效账本零写入 (验收13补强)

DIAGNOSTIC 阶段的 execute 成交**不进正式账本文件**: state 快照初始化到 `coord_state_rehearsal.json`, fills 落 `ledger_dual_cycle_v2_fwd_rehearsal.jsonl`; 正式 `coord_state.json` 与 `ledger_dual_cycle_v2_forward.jsonl` 在准入(FORMAL_ADMIT.json + `--formal`)前保持"从未自动成交"的原始态。链记录(审计档案)始终写并标 `ledger_target`。实弹测试断言: rehearsal 成交 fills≥3 且**正式账本 jsonl 不存在**。修复过程自曝一处缺陷: rehearsal 路径曾写成模块级常量导致 tmp 测试污染真实目录——已改运行期解析并清理, 测试后真实目录核验无残留。

## 6. 完整 T/T+1 验证记录 (实盘)

- **实弹测试(真实R31数据, 验收12)**: `test_p01_real_panel_execute_fills_raw_open` —— 真面板 plan(2026-10-16)=FORWARD+DIAGNOSTIC(buys 5), execute(2026-10-19) 用增量 raw open **真实成交 fills≥3 且逐笔价格=open**(结构性≠close); `test_recovery_*` 两条崩溃窗口防线锁定。此前合成面板测试因引擎无候选导致成交断言空洞通过——已由实弹测试暴露并补救(诚实记录: 该缺陷是负例方法论自身跑出的, 非审查者指出)。
- **失败恢复(指令5)**: PLAN已上链但state崩溃 → 重复plan拒绝rc=7并指引 `--recover`(链记PLAN_RECOVERY不重复PLAN, 重建冻结plan哈希逐笔一致); 账本已推进但链无EXECUTE(半执行) → rc=7拒绝自动重放(防双记账, 须人工对账)。
- T=2026-10-16 (REPLAY, 链上 seq1 完整): 5 笔 LONG_CYCLE 买入 19,000.74/20,000 (5% 现金预留精确), 36 候选, 0 卖出。
- T+1=2026-10-19 raw open 增量抓取: 15:50 定时执行(盘中零行fail-closed已实测)。
- 15:50 自动 `fetch+plan 2026-10-19`: 首条真 **FORWARD** PLAN 记录(DIAGNOSTIC 门), 含全部时间证明/模块审计字段。
- 16:10 自动 `execute 2026-10-19`: 对 10-16 计划做 raw-open 延迟重建 → 链上 EXECUTE — 即"一条完整真实数据 T 计划/T+1 执行验证记录"。


## 7. 遗留 (非本批范围, 如实声明)

- 预测有效性 NOT_CLAIMED 不变: CALIBRATED=False, STRICT 四闸门阻断(双引擎 strict 候选=0), FORWARD 样本量=0(今晚为首条)。
- 实时(09:40 盘中)执行价源未接入——按裁定采用收盘后 raw-open 延迟重建替代并显式标注, 不声称实时。
- FORMAL_ADMIT.json 准入文件待复核者对本批验证后决定。
