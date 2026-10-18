# 阶段A回归基线记录 (2026-10-18)

## 既有测试套件
```
python -m pytest tests -q   →  48 passed, 1 warning, 27.2s   (exit 0)
```
完整输出: `pytest_baseline_20261018.txt`。覆盖: 美股 cycle_portfolio/HOLD90/120 哈希锁+
原子性(test_cycle_portfolio)、执行器账本全语义(test_ashare_executor)、日历/池/管道
(test_ashare_data)、engine/nowcaster/ledger。此为 §12-10"零退化"的基线快照, V2 每阶段结束
重跑比对。

## 显式标注: 测试套件自带副作用(已知缺陷, 不静默, 本阶段不修改旧测试)
`tests/test_ashare_data.py` 运行期间会调用 `ashare_data_pipeline` 审计函数, 重新生成
`output/ashare/data_audit.json/.md`——内容不变但 `generated_at` 时间戳更新, 导致基线
校验器一度把这两条冻结路径判为脏(C2/C4 FAIL 的根因, 已定位)。
处置: 回归运行后由维护者 `git restore` 复位(本阶段已做); 校验器持续监控。
**不修改测试来消除该副作用**(既有回归测试不得为通过而降低要求, §1.1)。

## 工作区脏文件判定与复位(阶段A(1)完成记录)
接手时工作区不干净(均为今日其它会话产物, 已全部固定证据后复位到 HEAD):
- `swing_m1_expt_True_monthly_3f_daily_cap0.json`(M, 11:57 F2重放侧效, 53MB)→统计已存档 phaseA_evidence/
- `data_audit.json/.md`(M, 14:04 重生成, 仅时间戳差)→复位
- `swing_dd_attribution_v2.json`(M, **10-17 20:29 被覆写为 `{}`**)→自 HEAD 复位(完整内容恢复)
- `cycle_system/sws_r37d_final.py`(D, 被删除)→自 HEAD 复位
- 未跟踪残留(保留不动): today_view.py/json、_panel_cache.parquet、
  swing_m1_m1_True_daily_3f_monthly_cap0.json(19:29 探索性 m1 重跑侧效)、.acl-recovery/

当前状态: 冻结路径零修改(git status 干净), B0/B1/F2/manifest/F1/flow_audit/dd_attr 等
产物与 HEAD blob 一致, 美股两文件哈希锁通过。
