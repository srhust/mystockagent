# 账本取证注记 — shadow_run 81日产物污染 + long_backtest 自洽通过 (审计阶段1验收①)

日期: 2026-10-19 | 工具: `tools/ledger_selfcheck.py`(tools域新增, 预测模块零触碰)
产物: `independent/ledger_selfcheck_adj.json`

## 1. long_backtest(2020-06→2026-10, 单次运行): 验收第一关**通过**

| 检查 | 结果 |
|---|---|
| C1 现金流水重放(nav0+Σcash_delta == nav_curve.cash 逐日) | ✓ 1545日零失配 |
| C2 NAV构成(nav == cash + Σshares×close_adj 逐日) | ✓ 零失配 |
| C3 trades.csv ↔ ledger jsonl 逐笔(plan_hash/股数/名义额) | ✓ 40笔全对齐 |
| C4 股数守恒(买卖累计 == positions_json 存量逐日) | ✓ 零失配 |
| plan_hash 重复 | 40行/40唯一哈希 ✓ 单次运行纯净 |

## 2. shadow_run(81日旧产物): **账本被多次运行叠加污染** — -13.83% 的账面可信度再添独立证据

- `ledger_dual_cycle_v2_shadow.jsonl`: **151行 / 仅81个唯一 plan_hash / 45个哈希重复出现≥2次**;
- 成交日期域止于 2026-06-16, 与末次运行 trades.csv(至06-30) 天然错位(C3: 账本含 trades 没有的 601899/000878/300164… 等历史运行笔, C4: 同 code 股数出现 2-3 倍虚增);
- 根因: `ashare_executor.Ledger` 以 `ledger_{name}.jsonl` 为固定路径**追加写**, 同一 name 重跑不清空; 历史上 phaseF/实验多次以同 name 在同目录运行, trades.csv/nav_curve.csv 每次**覆盖**而账本**累积**——三文件不再是同一次运行的快照;
- 结论: **81日 SHADOW 的 -13.83%/NAV 曲线只能视为"最后一次覆盖运行+污染账本的混合读数"**, 连作为旧口径策略表现都不严格成立(与审查§5-1"旧实现模拟结果"判断互相强化)。
- 修复路径(阶段1重跑时执行, 不在本轮改执行器): 重跑 81 日 RAW 版必须用**全新隔离 ledger_name/目录**(工具层保证), selfcheck 四关通过后才发布新旧对照。

## 3. 复用边界

`ledger_selfcheck.py` 参数化价格域(adj=旧口径核对 / raw=RAW重建核对), 接受任意 run 目录:
`python -X utf8 cycle_system/ashare_v2/tools/ledger_selfcheck.py [run…] [--raw]`
今晚 rehearsal 账本与明晨 RAW 重跑产物都过同一工具, 保证验收第一关口径一致。
