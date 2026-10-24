# PHASE5_M4_INDEX — 第五阶段裁定条款 → 证据落点一页链（2026-10-24 编制）

裁定原文：2026-10-20 审查者《PHASE5_M4》（hash c892a089d623ff51…，M3_APPROVAL 绑定验证链生效中）。
基线：ashare-v2@6967845。全部提交在 ashare-v2 分支。

| 条款 | 要求锚点 | 证据落点 | 状态 |
|---|---|---|---|
| **A1** | 官方区间池/四区分/禁未来排除/UNKNOWN | `HISTORICAL_UNIVERSE_REPORT.md` §2（1998-2026 全表）+ ACCEPTANCE `A1_five_dims_audit`（代码变更维 UNKNOWN-by-design） | ✅ 声明合规 |
| **A2** | 八项修复逐条 | ACCEPTANCE `A2_eight_fixes_audit`（代码行级+实战双证表，COVERAGE 10:10） | ✅ 8/8 |
| **A3** | 四批+QC 门+预热+R31 交叉 | `M4_BATCH_COVERAGE_REPORT.md`（批0-3 结案表；批4 sina 七年表+R31 356,220 对零冲突）+ `QC/coverage_union_*.json` 22 年 | ✅ 主判据达成 / 🟡 union 尾巴待窗 |
| **A4** | 十指标×年/不以行数证完整 | COVERAGE 09:35 表（2020-26）+ 各年 coverage_sina_{Y}.json + LC/FV 列注记 | ✅ |
| **重大缺口暂停** | 不赶进度 | 12 连拒定性+低频自环（不 chase 记录在 COVERAGE 尾注） | ✅ 执行中 |
| **B** | 五路+区间+UNKNOWN+独立接口+不停价格 | `PIT_INDUSTRY_REPORT.md` §1-4（五路状态表+27 年曲线）+ ACCEPTANCE `B_five_paths_audit` | ✅ 主源达成 |
| **C** | 15:52 单bounded/阻断不切源/10-19 LATE/禁高频/冲突仅登记 | `FORWARD_DAILY_STATUS.md`（四日链）+ `LIVE_*.log` + ACCEPTANCE `C_forward_six_rules_audit` | ✅（FORWARD=0 条件未触发形态） |
| **D** | R0/R1 不改/新数据先五检/不称完整收益/禁寻优 | ACCEPTANCE `frozen_proof`（EMPTY_DIFF）+ `five_check_sina`（98 万行零违例）+ 各报告 narrative_ban 原文 | ✅ |
| **E** | 七交付物+逐批独立提交 | 本索引 §文件清单；commit 链每批独立（见 git log）| ✅ 1-5 定稿/6 滚动/7 待翻 CLOSED |
| 红线 | 16 冻结/FORMAL_ADMIT 关/无收益叙事/测试数≠有效性 | `frozen_proof`+全文措辞审计（无"改善/提升收益"表述）+ 修正账 23 条自纠链 | ✅ |

**文件清单**（output/ashare_v2/independent/）：M4_BATCH_COVERAGE_REPORT.md · HISTORICAL_UNIVERSE_REPORT.md · PIT_INDUSTRY_REPORT.md · CORPORATE_ACTION_GAPS.md（+TAX_TIMELINE_PREREG.md 附件）· DATA_CONFLICT_REPORT.md · FORWARD_DAILY_STATUS.md · M4_ACCEPTANCE.json · CORRECTIONS.jsonl(23) · STANDING_SNAPSHOT.md

**未闭合清单（如实）**：①批4 死券 union（自环 11648 等 baostock 窗口）②CA 2020-2026 事件段（同通道排队）③十指标 union 列 ④沪市 ST 全池 isST 对账批 ⑤B3 主营公告路（未立项）⑥配股第二源（GAP 未立项）⑦601607 型异常券续登。

**M4 终局判据**：①-③ 落位即批4 CLOSED_FINAL→ACCEPTANCE 翻 CLOSED；④-⑦ 属声明内后续研究口径工程，不阻断 M4 判据。
