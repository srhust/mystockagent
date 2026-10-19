# STANDING SNAPSHOT — Ashare V2 第四阶段（2026-10-19 20:40 更新，裁定 c182102+后）

## 状态行（裁定）
`ENGINEERING_AUDIT_PASS / RESEARCH_REPLAY_ONLY / FORMAL_ADMIT_BLOCKED`。主线审计通过；工作包 A 已交付（PATHWISE/降级/会计边界）；**E-1 时间语义已实装**（commit 见下）：`_kind_refined` 三值判定 FORWARD=signal>锚 且 wall_clock<exec_day 09:25；晚生成=LATE_RECONSTRUCTION 且 FORMAL 拒绝 rc=8。100 passed。

## 明日 15:50 / 16:10 执行序（裁定后修正版——10-19 不再是"首条真 FORWARD"）
1. **单发探测**：`python -X utf8 _netprobe.py`（PROBE_OK 才继续；失败=仍疑似限流，记录后顺延，**严禁批量轮询**）。
2. 通了 → 两次 fetch：`forward_shadow.py fetch 2026-10-19`（补抓）与 `fetch 2026-10-20`（**当日盘后**）。
3. `plan 2026-10-19` → 预期 **run_kind=LATE_RECONSTRUCTION**（今晚生成的 10-19 信号晚于 10-19 开盘边界——裁定：这是合法演练记录，永不计前向样本；链上如实标注，formal 会被 rc=8 拒绝）。
4. `execute 2026-10-19` → 10-16 计划的 raw-open 重建（seq3/4，rehearsal 域，同样 LATE 类——"历史成交演练"性质，正是裁定 §② 的定性）。
5. `plan 2026-10-20` → **这才是首条真 FORWARD**（exec_day=10-21，wall_clock 10-20 15:5x < 10-21 09:25 边界；audit 匹配+rehearsal 域）。
6. 收口：`tools/rehearsal_check.py`（H1-H5）+ `tools/redline_audit.py`（R6 须 TRUE）+ verify/head seal；报告 §5 追加实况；清理 `_barwatch.ps1`/`_netwatch.ps1`（`_netprobe.py` 留库）。
7. **10-21 起每日**：`execute 当日`（重建前一日 FORWARD 计划=真前向执行样本）+ `plan 当日` → 自然三日以上 FORWARD 积累开始。

## 若明日仍被封
- 记录事实即可；live 顺延不破坏任何账本语义（exec_day 由 10-16 计划固定，历史日线补抓确定性）。
- 可选通道排查：换网络出口/代理软件恢复（用户侧动作，报告中已注明这是环境依赖）。

## 关键文件速查
| 用途 | 路径 |
|---|---|
| 总呈报告（审查入口） | `output/ashare_v2/independent/AUDIT_FINAL_backtest_credibility.md`（§0 答案/§1-3 阶段/§4 验收/§5+5b B 线/§6 决策点/附录 A commit 索引） |
| RAW 归因 | `independent/raw_attribution_report.md` + `raw_bt_metrics.json` + `trade_pairing_81d.json` + `raw_trade_stats.json` |
| 状态机 | `independent/risk_lockout_diagnosis.json` |
| 点名归因 | `independent/big_loss_attribution.json` |
| 因子连续性 | `independent/adj_factor_continuity.json` |
| 账本取证 | `independent/ledger_forensics_note.md` + `ledger_selfcheck_adj.json` |
| 红线快照 | `independent/redline_audit.json`（复跑：`tools/redline_audit.py`） |
| RAW 重跑引擎 | `tools/raw_shadow_runner.py`（`2020-06-01 2026-10-16 [--engines LONG_CYCLE|FAST_V]`，跑前删旧输出目录防账本追加污染） |

## 红线（任何时候不得违反）
预测 16 模块哈希不变（RAW=tools 子类已证）；FORMAL_ADMIT.json 不得创建；正式 state/正式账本准入前零写入；收益数字只作来源分解不作性能判断；链记录不可删改（rc3/4/6/7 如实报告）。

## 测试基线
**100 passed**（tests/ashare_v2 全量，20:35 复核，含三日实弹 + `test_e1_forward_time_boundary`）。redline_audit R6=TRUE 同步复核（prediction_mismatch=0，FL/16 元组零触碰——E-1 全部在 tools 域）。
