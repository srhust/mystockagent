# 前向运营交接手册（Release Gate 通过 · bdfbafd→8c7b36d 裁定 2026-10-15）

## 冻结身份
- 策略：V2aF-Recycle，HOLD 90（挑战）/ HOLD 120（基线），scorer=12m/edge，COST=0.0015，DEDUP=60，14 标的。**24 个月内不因短期表现修改。**
- 凭证：`launch_credential_v3.json`（文件级 SHA-256 绑定执行器+驱动器，self=ef4713c39c1e）。代码任何改动 → 哈希失配 → settle 自动拒绝 → 必须重签新版本凭证。

## 每日程序
1. **T 日收盘后**：`python scripts/run_shadow.py plan` —— 由独立 NYSE 日历解析 T+1，生成并持久化冻结计划（created_at=UTC 真实时间）。
2. **T+1 收盘且数据完整后**：`python scripts/run_shadow.py settle` —— fail-closed 身份 → 事务恢复扫描 → 双配置同步 → 预检查（事前计划存在 + Close(T)≤created_at(ET)<Open(T+1)）→ 双配置 dry 校验 → BOTH_PREPARED → 90 结算 → 120 结算 → BOTH_COMMITTED。
3. **无参数**：只读 status。`replay` 模式永不计分。

## 事务语义（协议声明）
- `TXN_ABORTED` = 本次提交尝试失败；**资金状态不回滚**。恢复采取"向前补齐"：依赖原冻结计划与原状态身份，幂等执行（EXECUTED 按 plan_id 去重、状态哈希+逐笔成交核对）。
- 恢复禁止重新生成计划；冻结计划缺失 → 拒绝并人工裁决。
- 已验收历史行情的基准段指纹每日复核，任何修订 → `DATA_REVISION` → 拒绝计分。

## 历史缺口裁定（2026-10-14 起）
- 10-14（及任何错过事前计划窗口的交易日）为**不计分引导阶段**：不得追认为前向样本，不得补造计划。
- 引导期允许不计分的状态推进（replay/scoring=false）；**首次正式签发时**，`launch_credential_official.json` 记录**真实计分起点**的两组完整状态哈希与 NAV（区别于 v3 中 10-13 的引导起点哈希，后者保留为历史来源），forward_start=首个合格事前结算日，一次性签发、永不覆盖。

## 首日正式样本确认条件
两组均成功结算 → 实际计分起点 NAV 与状态哈希正确记录 → 正式凭证仅签发一次 → 该日成为 24 个月观察的第一个正式样本。

## 日常监控职责（取代策略调参）
数据质量（raw 到达性/缺日/NaN/修订）、计划时效（created_at 窗口）、模拟执行对账（逐笔成交/NAV/费用/状态哈希）、异常事务审计（TXN_ABORTED/DATA_REVISION/PLAN_MISSING）。
