# M3 扩量评审包索引（2000 年数据扩展，供审查者裁决）

评审对象：是否批准 2000-2020 历史数据**全量扩量**（M4）。裁决选项：批准全量 / 批准分批 / 拒绝并回 POC。
纪律：POC 先行严禁全量（裁定原文）；本包=POC 全部证据的一页索引。

## 一、POC 硬闸 A1-A6 结果总览

| 闸 | 判据 | 结果 | 证据文件 |
|---|---|---|---|
| A1 数据冲突 | 重叠段 sina 面板 vs baostock 差>0.5% <1% | **0.53%（188 行 1 失配）PASS** | `data/ashare_history_2000/QC/poc_report.json`；失配 100% 归因=sina 缓存滞后（`independent/crosscheck_2026-10-16.json`，更正 #9；次日 API 自修正为 baostock 同值 3.66=判定获独立证实，附录 D） |
| A2 退市/边界 | 无 look-ahead 的终止取证 | **PARTIAL**：主表 diff 190 券落盘；2 只真实退市日确定（600002@2006-04-24/600065@2007-12-13），4 只窗尾值按纪律标 UNKNOWN 待扩窗 | `QC/delist_boundary_probe.json`、`security_master/master_diff_2005_2026.json` |
| A3 企业行动 | 送转/拆股守恒<0.01 | **PASS（分红/送转域）**：156 涉事券 100% COVERED、591 事件、R1_CAI 重放 C1-C4 全 n=0 all_ok TRUE；**配股=RIGHTS GAP_UNVERIFIED**（涉配股期维持 UNVERIFIED+限制声明） | `full_replay_pool/coverage_report.json`、`independent/CA_BT_integration_report.json`、`independent/CA_AVAILABILITY_baostock_2026-10-20.json` |
| A4 行业 PIT | UNKNOWN 率量化 | **已量化：baostock 行业=当前快照, 历史 PIT UNKNOWN 率 100%（已知缺陷）**——扩量后行业语境特征在 2000-2020 段不可用，除非批准申万历史/公告重建第二源 | `poc_report.json.acceptance`、`HISTORY_2000_POC_PLAN.md` §5 |
| A5 通道纪律 | 零熔断死循环、错误可解释 | **PASS**：M1 6 请求+M2 136 请求+pacing 修正后 0 blocked 0 错误；退市边界探针 ~42 请求 | guard stats 内嵌各 json |
| A6 冻结参数只读 | 扩展数据不得触碰 R0/R1/16 元组 | **PASS**：扩展全部落 `data/ashare_history_2000/` 独立层；R0 4 目录+r1_bt 零触碰（红线审计 R 项 ok） | `independent/F1_r0_unchanged.json` 同纪律 |

## 二、通道事实（更正 #12 后）
- baostock 独立通道：登录即用、日行 tradestatus/isST 全字段、退市历史在库、分红送转官方、**含 2026-10-19 当日行**（与 sina 单发互证 0.000% 差，5/5 抽样）。
- akshare：sina 接口可用（248/251 ok），**东财 push2his 被远端拒**（16 元组 fetch_day 恰用该源 → FORWARD 阻断中，见决策⑤）。

## 三、遗留议题（请审查者表态）
1. **A2 扩窗**：4 只 UNKNOWN 退市券的准确终止日（追加 ~60 请求）——批准即执行；
2. **A4 行业源**：是否批准申万历史/公告重建作为第二源（否则 2000-2020 段行业特征=UNKNOWN）；
3. **M4 分批规模**：全量 ≈5500 券×21 年，按 pacing 0.35s 估 ~40h 机时——若批准,建议按年切片分批（每年 ~2h 可中断+QC 门）；
4. **决策⑤**（非本包但同期悬置）：FORWARD 源三方案 A/B/C（`DESIGN_FORWARD_SOURCE_TRILEMMA.md`，默认 A+C 执行中）。

## 四、本 session 未动的东西（红线自检）
R0 预测 16 冻结（mismatch 空）；FORMAL_ADMIT 关闭；扩展未生成任何新收益叙事（A3 数字仅账本判定+路径敏感性披露，narrative_ban 生效）；全量拉取**未启动**（等 M3）。
