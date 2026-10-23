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

### 2026-10-20（周二，今日）——**结果实录**
- 15:52:51 主链 fetch 2026-10-20：**rc=1 零成交行**（251 券执行全过、EM 接口拒供）→ `RuntimeError 前向数据不完整(禁止生成计划)` 闸门阻断 plan ✓ **不伪造**。
- C 层 15:54 sina probe 10-20：**248 no_data / 0 network**（探针健康，sina 当日数据 15:54 未上架=数据事实非通道错误；16:40 后复测）。
- C 层 16:02 baostock crosscheck：5/5 **TRADE_DATE_NOT_IN_BAOSTOCK**（官方日更延迟，非错误标注）；**通道自愈确认**（前僵尸态已清，会话健康）。
- **C 层 19:3x 夜测（方案⑤定案实录）**：sina probe **248/248 ok**（收盘 3.5h 后=日更窗口 19:30 实定）；baostock crosscheck **5/5 单日实取**（000002 close 3.45/tradestatus 1）+ sina-hfq endpoint 仍缺当日=**DC-7 同机构双 endpoint 时点独立定案**。当日数据真实性获 baostock 第三方旁证；FORWARD 入账不变=0（C 层=数据事实非 FORWARD 事件）。
- 16:20:40 LATE 彩排 fetch 10-19：同闸门**如实阻断**（无 plan 无 seq——LATE 实体化条件未满足，宁缺毋伪）。
- **今日结论（按预注册判定）**：FORWARD seq2 **未成立**（rows=0）→ **当日 FORWARD=0 + 正式阻断实录**；链仍单条 seq1 REPLAY；16:20 后无新增 plan/seq 污染 ✓。
- **FORWARD 累计：0**（诚实计数，EM 接口级封锁持续中的正确状态）

### 2026-10-21（周三，交易日）——当日计划（预注册）
- 15:50/15:52 **单次有界** EM fetch（通用哨兵 `_sentinel_day.py` 晨间挂起；rc99 超时兜底/零重试已固化）。
- 成功→plan+verify+rehearsal+redline（验证早于 10-22 09:25 成交边界）；零行→阻断如实当日 FORWARD=0，**严禁切源伪造**。
- 16:10 T+1 execute：**昨无 plan=正确空转**（禁补造）。
- 19:30 C 层 probe+crosscheck 常规备份（不入 FORWARD）。

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

### 2026-10-21（周三）——**结果实录：FORWARD=0（阻断连续第 2 日）**
- 15:52:5x 通用哨兵单次有界 fetch：**零成交行** → `RuntimeError 2026-10-21 零成交行抓取…禁止生成计划` 闸门阻断 ✓ **未切源未伪造，无重试**（LIVE_2026-10-21.log 15:54 行实录）。
- 定性：EM 拒供 10-20/10-21 **连续两交易日**（错误签名一致 network/零行）=接口级持续封锁，非当日抖动；前向链 seq1 后无新增。
- 16:10 T+1 execute：昨（10-20）无 plan=空转正确。
- 19:30 C 层照挂（sina/baostock 旁证当日真值，不入 FORWARD）。
- **FORWARD 累计：0**（两日阻断如实计数）

- **19:3x-19:4x C 层（哨兵位手动替补——哨兵进程 19:0x 被宿主回收实录）**：probe **248/248 ok, network=0**（10-21 数据 sina 上架✓）；`sina_vs_baostock_sample` **全 diff_pct=0.0**（000002=3.46/000012=3.52/000050=5.79…双源互证当日真值，guard 496ok/2fail/breaker CLOSED）。当日 FORWARD 仍=0（C 层=数据事实）；两交易日数据真实性均获异源旁证。**教训追加**：Start-Process 托管亦可被宿主进程树回收（19816 无栈死亡）——时点动作以 schedule 提醒为准、常驻仅辅助；明日 08:30 晨挂提醒已在（22748b01）。


## 2026-10-22（FORWARD=0，连续第 3 交易日）

- 15:52:5x 哨兵（11496，08:10 晨挂）单次有界 EM 抓取 → **零成交行 → 闸门阻断，无重试**（`LIVE_2026-10-22.log` + stdout 双证："gate stands, no retry (ruling C single bounded)"）。
- 与 10-20/21 同型：EM 快照接口对该日持续无供，**正式阻断而非切源伪造**（裁定 C）。C 层 19:30 sina probe/crosscheck 在轨（哨兵自动段）。


**19:47 C 层（哨兵自动段全绿，进程 11496 善终 DONE 19:47:30）**：
- probe（渠道实时）：pool 250 → **248 ok / 2 format / network=0**（10-22 bar sina 渠道在档✓）
- crosscheck：baostock **5/5 今日真值**（000002 收 3.31 等，tradestatus 全交易）；`sina_close=None` 的语义=**现役 STOCKS 面板无 10-22 bar**（面板停更属预期——FORWARD=0 无入库，非渠道缺数；probe 已证渠道有数）。两口径分清防误读。
- EM 侧：15:52 单次有界零成交行 → 阻断三日连（FORWARD=0；C 层双源持续证明**市场数据本身每日齐备**，缺的只是东财快照接口供给）。


## 2026-10-23（FORWARD=0，连续第 4 交易日）

- 15:52:37 哨兵（33776）单次有界 EM → 零成交行阻断（gate stands, no retry；与 10-20/21/22 同型第四日）。C 层 19:30 自动段在轨。
- 19:4x C层（哨兵33776自动段）：probe **248/250 ok**（同昨日，渠道今日 bar 在档✓）；crosscheck baostock 5/5 今日真值（000002 收 3.37），面板 MISSING=停更预期语义。EM 封锁不污染旁证：市场数据每日齐备第四日连证。
