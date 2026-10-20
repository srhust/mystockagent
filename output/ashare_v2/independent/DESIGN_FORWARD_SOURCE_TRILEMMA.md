# 决策材料⑤：FORWARD 数据源封锁下的三方案（2026-10-20，审查者裁决权）

背景（更正 #12 取证）：16 元组 `forward_ingest.fetch_day` 数据源=东财 push2his，该接口
现被远端连接拒绝（单发直连 RemoteDisconnected，非 burst/非 IP 级/非 akshare 全局）。
sina `stock_zh_a_daily` 与 baostock 独立通道正常。裁定 E 的 run_kind 语义要求 FORWARD
必须由 fetch_day 原链路在开盘边界前产出数据+计划——**数据源封锁直接阻断 FORWARD 计数**。

## 方案对比

| 方案 | 内容 | 保什么 | 代价 |
|---|---|---|---|
| A. 冻结纯度 | 16 元组不动，等东财恢复；期间无 FORWARD 样本 | FORWARD 定义纯度（单一数据源链路审计） | 前向研究停摆时长不可控（东财封锁已 4 天+） |
| B. 批准换源 | fetch_day 改 sina/baostock（或双源多数派） | 研究连续性 | **动 16 冻结域**=本 session 权限外；历史 FORWARD 与新 FORWARD 源不同构，链审计口径分裂 |
| C. **平行事实层**（推荐预备） | 16 元组不动；每日收盘后 `sina_channel_probe`（全池 251 双请求错误分布）+ `live_crosscheck`（baostock 对账抽样）各跑一次，产 `sina_channel_probe_<day>.json` 作 **PROBE_VERIFIED_DATA_FACT 备份**——**不生成计划、不入 FORWARD 计数、非 run_kind** | 数据事实连续性（东财若长期封锁，未来换源/重放有已验证的逐日源数据）+ 纯 FORWARD 定义零污染 | 每日 ~12 分钟脚本时间；无代码改动（两工具已在役） |

## 方案 C 的边界（防误读）
- PROBE_VERIFIED_DATA_FACT **永不冒充 FORWARD**：它无 plan、无执行、无链上 seq；仅是"该交易日全池数据存在且双源一致到什么程度"的审计备份。
- 若审查者日后批准 B：C 层积累即换源验证的天然对照组（同一批日期三种源逐券差分布已在档）。
- 若东财恢复：fetch_day 链路无缝回到主轨，C 层继续作每日对账证据（live_crosscheck 已入明日序列 2b）。

## 今日（10-20）动作按序
1. 15:50 后 `forward_shadow.py fetch 2026-10-20`——东财若仍拒→零行闸门如实登记（阻断=设计正确）；
   若通→plan 2026-10-20=**首条真 FORWARD** 全链走 16 元组。
2. 收盘后无论 1 结果如何：C 层两跑（sina probe 全池 + baostock crosscheck）=10-20 数据事实备份。
3. rehearsal_check/redline_audit/verify seal 收口。

裁决权在审查者：A/B/C 或组合。本 session 默认执行 A+C（零冻结域触碰），B 不申请不实施。
