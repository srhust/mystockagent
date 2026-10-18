# Ashare Dual-Cycle V2 — 运行入口(随阶段推进补全)

| 命令 | 阶段 | 说明 |
|---|---|---|
| `python cycle_system/ashare_v2/tools/baseline_integrity.py` | A ✅ | 冻结基线完整性验证(C1–C7), 结果写 `output/ashare_v2/baseline_integrity/` |
| `python cycle_system/ashare_v2/tools/f2_repro_check.py` | A ✅ | F2 参数集独立重放(⚠ 副作用: 重写非冻结的 cap0 侧效文件, 跑完 `git restore output/ashare/swing_m1_expt_True_monthly_3f_daily_cap0.json`) |
| `python -m pytest tests/ashare_v2 -q` | A ✅ | V2 工程测试(随阶段增长) |
| `python -m pytest tests -q` | 每阶段 | 既有 48 测试零退化基线(注意: 会重写 data_audit.* 时间戳, 跑后 git restore) |
| `python -m cycle_system.ashare_v2.runner diagnostic\|synthetic\|shadow` | F | 三模式入口(待交付) |

设计: `DESIGN.md`。数据契约: `DATA_CONTRACT.md`。任务书: `TASK_SPEC.md`。
产出目录: `E:\mystockagent\output\ashare_v2\`。

**红线提醒**: 禁止修改 swing_m1/executor/tradeable_pool/stock_pipeline 四文件与全部
FROZEN_ARTIFACTS; 美股 HOLD 90/120 文件严禁触碰; 缺数据标 `DATA_BLOCKED` 不编造;
未经独立样本外验证不声明预测有效。
