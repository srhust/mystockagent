# STANDING SNAPSHOT — Ashare V2 审计（2026-10-19 晚 19:45 封账态）

## 一句话状态
审计主线**全部交付并机器验证**（三阶段+验收三关+红线 R6=TRUE+总呈报告+commit 索引，23 commits，HEAD==origin e46d8bf）；唯一开放项 = live FORWARD 记录，因**数据源侧 IP 限流**（本机被 eastmoney 封，直连/代理两路径均 RemoteDisconnected）今晚客观不可行，顺延明日。

## 明日 15:50 / 16:10 提醒到达时的执行序（勿凭记忆，按此做）
1. **单发探测**：`python -X utf8 _netprobe.py`（输出 PROBE_OK 才继续；失败=仍未解封，报告后等晚间/后日，**严禁 530 连发轮询**——今日 4000+ 次调用是限流主因）。
2. 探测通 → `python -X utf8 cycle_system/ashare_v2/tools/forward_shadow.py fetch 2026-10-19`（全量 530×2 调用仅此一次，收盘后历史日线确定性可得；meta 落 `output/ashare_v2/forward/incremental/2026-10-19.meta.json`）。
3. `forward_shadow.py plan 2026-10-19` → 期望 rc=0，链 seq2，字段核对：`run_kind=FORWARD`、`formal_gate=DIAGNOSTIC_REHEARSAL`、`ledger_target=REHEARSAL:*`、`data_fetched_at` 真实时刻、module_audit 预测 16 项 match=true。
4. 16:10 批次：`forward_shadow.py execute 2026-10-19`（10-16 计划 raw-open 重建，seq3，成交只进 rehearsal 域）→ 若 10-20 增量也已可得，plan 2026-10-20 走 rehearsal state（连续性由修复保证）。
5. **收口三件套**：`tools/rehearsal_check.py`（H1-H5 全 ok）、`tools/redline_audit.py`（R6 仍 TRUE）、`forward_shadow.py verify seq` + head seal 汇报；若 execute 产生了 ledger 行，抽 2-3 笔 fills 与 10-19 raw open 逐笔对照（数据在 rehearsal ledger）。
6. 向总呈报告 §5 追加 live 完成记录 → commit + push（清理 `_barwatch.ps1`/`_netwatch.ps1` 临时 watcher；`_netprobe.py` 保留在库——它就是本执行序第 1 步的探测工具）。

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
99 passed（tests/ashare_v2 全量，含三日连续实弹 `test_rehearsal_three_day_state_continuity`）。live 收口无需重跑全量（tools 无改动时）。
