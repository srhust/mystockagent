# FORWARD_DAILY_STATUS — 真 FORWARD / LATE / REPLAY 逐日台账（交付物 #6，第五阶段裁定 C/E）

## 0. 判读规则（预注册，读数前写死；延续第四阶段 E_LIVE 与裁定 C）
| run_kind | 成立条件 | 计数域 |
|---|---|---|
| **FORWARD** | fetch 行数>0 ∧ plan 于当日 15:52–16:30 生成 ∧ 链上标注 FORWARD ∧ wall_clock < 次日 09:25（T+1 成交信息边界） | FORWARD 计数 +1 |
| **LATE_RECONSTRUCTION** | 计划生成时刻晚于标的交易日（补录） | **永不入 FORWARD 计数**；rc=8 阻断 FORMAL |
| **REPLAY** | 研究窗重放（signal_day 在过去、批量重放语义） | 重放域，另账 |
| **PROBE_VERIFIED_DATA_FACT** | sina probe / baostock 对账产物 | 数据事实层，**无 plan、无 seq、不产生任何 run_kind** |

阻断记录 ≠ 零失败：FORWARD 抓取被数据源拒绝时，**如实记 FORWARD=0（当日）**，不得以 LATE/REPLAY 顶替。严禁重复高频探测东财（当日主链仅哨兵一次）。

## 1. 既有链上记录（forward_chain.jsonl）
| seq | signal_day | event | run_kind | wall_clock | 备注 |
|---|---|---|---|---|---|
| 1 | 2026-10-16 | PLAN | REPLAY | 2026-10-19T11:36 | 链头，rec_hash 2430092e…，36 候选 5 buys |

## 2. 逐日台账
### 2026-10-19（周一）
- FORWARD 尝试：**0 次**（当日为 LATE 彩排域；EM 封锁实录=更正 #12 取证，未做当日盘后 fetch）。
- LATE/REPLAY：seq1 属 10-19 执行的 REPLAY（10-16 signal）。
- C 层事实：sina probe 248/251 ok；baostock 对账 5/5 全等（crosscheck_2026-10-19.json）。
- **FORWARD 累计：0**

### 2026-10-20（周二，今日）
- 15:52 哨兵单次有界主链抓取（裁定 C "只尝试一次"）→ [待回填]
  - fetch 结果=____ / plan 生成时刻=____ / 链 seq2 run_kind=____
- 16:20 LATE 彩排哨兵（10-19 fetch→plan 预期 LATE_RECONSTRUCTION 或如实跳过）→ [待回填]
- C 层事实：sina probe 10-20 → [哨兵自动] / baostock 对账 → [若僵尸会话未清则顺延登记]
- **今日结论判定（预注册）**：FORWARD seq2 成立 = 行数>0 ∧ plan∈[15:52,16:30] ∧ 链标 FORWARD ∧ wall<10-21 09:25；否则 FORWARD=0 当日 + 阻断实录。
- **FORWARD 累计：____**（读数前不填）

### 2026-10-21（周三，计划）
- 若 10-20 FORWARD 成立：`execute 2026-10-21`（10-20 计划的真前向执行=T+1 raw open）+ 盘后 fetch/plan 当日 → FORWARD 逐日累积自本日启动。
- baostock 对账顺延项补跑。

## 3. 冲突登记（裁定 C：sina↔baostock 差异登记不覆盖）
| day | code | sina | baostock | 处置 |
|---|---|---|---|---|
| 2026-10-19 | （全 5 对账样本） | 一致 | 一致 | 无冲突 |
| 2026-10-16 | 000002 | 3.72（面板快照，保留未改） | 3.66 | 面板旧值**不覆盖**；akshare 10-20 晨自修正=3.66 获 API 级确认（附录 D） |
| [每日追加] | | | | |

## 4. FORMAL_ADMIT 恒关声明
本文档任何台账**不构成** FORMAL_ADMIT 证据；状态线维持 ENGINEERING_AUDIT_PASS / RESEARCH_REPLAY_ONLY / FORMAL_ADMIT_BLOCKED（裁定红线，M4 未完不得改预测规则）。
