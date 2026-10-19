# Ashare V2 回测可信度专项审计 — 总呈报告（第三轮指令收口）

基线 `578418e` | 分支 `ashare-v2` | 审查指令日 2026-10-19 | 本审计全程：预测规则零改动、正式 FORWARD 绩效账本零写入、`FORMAL_ADMIT.json` 保持不存在、不对任何收益数字作性能判断（只作来源分解）。

---

## 0. 审查核心问题的最终答案

> "81 日 SHADOW 的 -13.83% 中多少来自真实策略决策、多少来自价格/记账/风险状态机实现问题？"

**-13.83%（旧）= -20.93%（RAW 诚实账本）+ 7.1pp 虚高 mark 幻觉**。实现问题**抬高**了旧读数而非压低；且口径污染通过"软化锁死时点→拖长持仓三个月→多亏 13.04pp（B 段）"的间接通路**扩大**了真实亏损。三段归因（`trade_pairing_81d.json`，逐项可复算）：

| 来源 | 量化 | 证据 |
|---|---|---|
| 真实策略决策 | 锁前 14 笔的方向性亏损主体；601069/002207 adj/raw 双域 MAE 几乎一致（-39.6% vs -39.9%）→ 方向错误与口径无关 | `big_loss_attribution.json` |
| 价格/记账实现 | A 段 +20.13pp（close_adj 成交+估值虚高 + 隔夜跳空成本隐藏，同源于 mark）；新旧决策集重叠仅 **1/35**——口径重排整个决策集 | `trade_pairing_81d.csv/json` |
| 风险状态机 | RAW 诚实读数 03-24 即 EXCEEDED、04-01 清仓恒平至窗末；旧虚高读数把锁死推迟到 06-08，多交易再亏 -2,607（B 段 -13.04pp） | `risk_lockout_diagnosis.json` |

---

## 1. 阶段 1（P0 回测修复）逐项对账

| # | 指令 | 状态 | 证据 |
|---|---|---|---|
| 1-1 | T 日收盘信号 / T+1 raw open 成交 / raw close 估值 | ✅ 新统一口径入口 = `tools/raw_shadow_runner.py`（tools 域 Runner 子类，**16 预测模块哈希不变**，与冻结前向审计兼容） | run 输出 + `output/ashare_v2/raw_bt/` |
| 1-2 | 复权价只用于技术信号；预算/整手/涨跌停/费用/NAV 全 raw | ✅ 信号域仍 close_adj 引擎原样；`plan_day(px=raw close)`、执行器原生日限价/整手/费用全吃 raw | 同上 + `trade_audit.csv` 每列 |
| 1-3 | adj_factor 除权连续性验证 | ✅ 恒等式 max_err=0.0、残差全零；**关键 nuance**：hfq 因子=滚动基准量（95.6% 行漂移≠除权），故 RAW 账本股数**不**按 factor 逐日换算；adj 对照轨按 T0 锚归一 | `adj_factor_continuity.json` |
| 1-4 | 81 日新旧逐笔对照 | ✅ 配对表 + NAV 差两段分解（A +20.13pp / B -13.04pp） | `trade_pairing_81d.{csv,json}` |
| 1-5 | 每笔保存 T 信号/成交原价/估值原价/拒单/资金变化 | ✅ `trade_audit.csv`：signal_day/exec_raw_open/val_raw_close/val_adj_close/prev_raw_close/fees/cash_delta/plan_hash/拒单原因全列 | raw_bt 各 run 目录 |
| 1-6 | rehearsal 连续状态分叉修复（成交进次日 plan；正式账本零写入） | ✅ `_state_domain()`：DIAGNOSTIC 下 plan/execute 同读写 rehearsal 域；正式 state/正式账本准入前**全程不可达** | commit 459aa6c + 测试 |
| 1-7 | ≥3 交易日 plan→execute→plan→execute 实弹 | ✅ 测试内真实执行链：D2 成交进 state（cash/pos/日序推进断言）→ D2 plan 从成交后状态出发 → D3 再执行；正式 state/账本 jsonl **全程不存在**断言；链 verify seq4 | `test_rehearsal_three_day_state_continuity`（99 passed 全量绿） |

## 2. 阶段 2（完整历史回测，参数冻结）

三模式 2020-06→2026-10（1,545 日，RAW 域，`raw_bt_metrics.json`）：

| | 双引擎 | LC 单独 | FV 单独 |
|---|---|---|---|
| 累计/CAGR | -20.80% / -3.59% | -21.40% / -3.71% | -20.80% / -3.59% |
| MDD / Sharpe / Calmar | -22.09% / -0.78 / -0.16 | -21.62% / -0.42 / -0.17 | -22.09% / -0.78 / -0.16 |
| 成交/换手/利用率 | 52 / 1.82 / 2.9% | 18 / 0.51 / 6.9% | 52 / 1.82 / 2.9% |
| 最长连续空仓 | 1,446 天 | 1,286 天 | 1,446 天 |

- **MERGED ≡ FV_ONLY 逐列全等**：机制=结构性时序错配（LC 候选 77,791 行覆盖 93.6% 交易日，但 FV 先入场并于 2020-09-14 触发锁死；LC 首候选 2021-01-13 当日已 EXCEEDED）。LC_ONLY 反证其入场窗口真实存在（2021-01→06，9 buys）。
- 601069/002207 独立归因：买入证据（LC/EXPANSION/TREND_CONTINUATION/7.19/7.22pts）、行业态、退出信号日→执行日（T+1 正常）、持仓期 MAE/MFE（601069 持 66 日 **MFE=0 从未浮盈**）——行业级退出条款救不了行业内单边弱个股，层级错位仅呈证据，不改参。
- 全部产物标注：REPLAY / 250 票幸存者池 / PIT 冻结快照研究模式 / 非前向非 OOS。

## 3. 阶段 3（风控状态机诊断，规则冻结）

`risk_lockout=true`，五段时序（多年 adj 域实测；RAW 域同构且锁死更早）：
S1 2020-10-30 NAV 破峰值 80%（-20.42%）→ S2 EXCEEDED 边界振荡（11-02 单日瞬时回 NORMAL 不构成恢复路径）至 **2020-11-11 起永久锁定 1,436 天** → S3 末笔强平 2020-11-17 → S4 清仓后 NAV 恒平（日收益 std=0；解冻需 +2.25%，零敞口在数学上永不可达）→ S5 锁后 84,486 候选/1,435 日全拒。
**恢复机制**：按裁定未立项——R0 参数一字未动，任何恢复版本须独立注册并与 R0 完整对照（决策点交还审查者）。

## 4. 验收三关

1. **新版账本自洽** ✅：RAW 三 run `ledger_selfcheck --raw` C1-C4 零失配、账本行数=唯一 plan_hash（无旧污染）；取证对照——shadow_run 旧账本 151 行/81 唯一哈希/45 重复（跨运行追加写污染，`ledger_forensics_note.md`）。
2. **81 日收益差逐项归因** ✅：§0 表 + A/B 两段分解。
3. **多年读数** ✅：§2 三模式全指标（按裁定不作性能判断，只作实现问题与决策问题的占比证据）。

## 5. B 线（今晚 live FORWARD 记录）状态

- 10-16 REPLAY PLAN 链 seq1 完好（head 2430092e…，verify ok）。
- **根因更正（18:52，诚实披露）**：14:56→18:41 共 21 轮 fetch 报"零成交行"，经定点排查真因是**系统代理故障**（`ProxyError: Unable to connect to proxy`，push2his.eastmoney.com 直连测试 3/3 全挂）——`fetch_day` 顶层文案把 530 笔网络层失败折叠成"零成交行"，**早先"akshare 落库延迟"的归因不完整**。旁证：18:45 另一接口路径成功取回 600000 的 **10-19 真实日线（开 9.72/收 9.78）= bar 早已落库**，通道挂除当晚即不可重试成功。watcher 已停（每轮 530×2 次无效调用）。
- 改进项（登记，明日 Addendum 窗口）：`forward_ingest.fetch_day` 零成交 raise 文案应附 missing 原因分布摘要（网络故障 vs 真无 bar 的区分），属错误可观测性修正不改规则，但该模块在 16 元组内——必须随 R0.4 登记，今晚不动。
- 15:50/16:10 提醒批次处理如实记录：fetch 未就绪→execute 拒绝执行（rc=6 路径，非 rc7，无半执行），链零写入。
- live 计划不变：网络恢复后 `fetch 2026-10-19` → `plan`（首条真 FORWARD，rehearsal 域）→ `execute`（raw-open 重建，seq3）→ 重跑 redline_audit/selfcheck 收口。若今晚始终不可恢复：顺延明日 cron，账本语义不受影响（exec_day=10-19 固定重建合法）。
- **终判（19:2x，排查史最后一步）**：`NO_PROXY=*` 绕开系统代理**直连仍 `RemoteDisconnected`** → 并非仅代理层故障，而是**本机 IP 被数据源侧限流/封禁**（当日审计自身累计 4,000+ 次轮询调用——15:xx-18:xx 每轮 530×2 的高频 watcher 是主要来源，**审计行为自身的网络副作用，如实记录**）。全部轮询已停（继续探测只会加重限流）；明日 15:50 起改**单发探测→通了才全量**的低频模式，10-19/10-20 增量一并补齐（历史日线确定性可得）。流程教训登记：数据源轮询须指数退避+频次上限。

## 5b. 开放待办（10-19 晚收口版）

已完成并入库：81 日逐笔配对表（`trade_pairing_81d.csv/json`，A/B 两段分解）、LC 挤出机制证据链（修正为**时序错配**：LC 首候选日即锁死日）、决策层平仓分布（`raw_trade_stats.json`）。

开放项：
1. **live FORWARD 记录**：被系统代理故障阻断（18:46 起 push2his 通道不通；10-19 数据本身已落库——600000 旁证开 9.72/收 9.78）。探针恢复通道即自动 fetch→plan→execute 收口；若今晚未补上，明日 15:50 cron 的 10-20 plan 会把 10-19 增量自动并入合并层（历史日线确定性可补抓，语义无损）。
2. **`forward_ingest.fetch_day` 可观测性文案**：零成交 raise 应附 missing 原因分布（网络故障 vs 真无 bar）。纯错误文案不改规则，但该模块在 16 元组内——是否随 Addendum R0.4 登记执行，交审查者裁定（今晚不动已明示）。
3. §6 决策点 1-4 待审查者回复。

## 6. 交还审查者的决策点

1. 风险状态机**恢复机制**是否立项（独立新版本 + R0 对照的规格与验收标准）。
2. FORMAL_ADMIT 准入条件是否按"三日 live 链记录 + selfcheck 全绿 + 本归因报告"复审（当前保持关闭）。
3. 81 日窗结论在 RAW 域复现了"锁死先行"后，长周期策略的**测量通道**问题（LC 时序错配）需要策略级还是状态机级修法——本审计仅呈证据，两案均可行但属预测/风险参数域，超出冻结边界。
4. 隔夜跳空成本（A 段）在 2026 样本显著、多年窗反号——live FORWARD 记录攒够后建议按执行时刻重估该成本分布。

*本报告由 pwsh 全链可复算：`raw_shadow_runner.py` / `ledger_selfcheck.py` / `raw_old_pairing.py` / `risk_lockout_diag.py` / `big_loss_attrib.py` / `adj_factor_check.py`（均 tools 域，审计 warn-only）。*

## 附录 A — 审计留痕 commit 索引（`578418e..HEAD`，19 个，按时间序）

| commit | 主题 |
|---|---|
| 4ee8786 | A线全历史 REPLAY 读数（-21.76%/锁死5.5年/LC零成交，审查裁定后当日交付） |
| 5a5f1dc / 9d5ed16 | engine_eval 多年接入 + LC"双重时间错位"归因精化 |
| df1a05b | 表述边界纪律（raw-open 重建≠实时可成交性）入 disposition §6 |
| 459aa6c | **§6-2 rehearsal 状态分叉修复 + 连续三交易日实弹测试**（99 passed） |
| 7f1275d | **阶段3 risk_lockout=true** 五段时序复现 |
| 3ce2e66 | 阶段2 601069/002207 独立归因（双域 MAE 一致性分解决策vs口径） |
| a704e89 | **验收第一关 ledger_selfcheck** + shadow_run 账本污染取证 |
| cc9cff2 | **阶段1-3 adj 因子连续性**（hfq 滚动基准量发现→股数不换算定稿） |
| d4dfb00 | **阶段1核心：raw_shadow_runner**（RAW 交易域重跑引擎，16 模块零改动） |
| 1df35e1 / e80e21f | 阶段1/2 主交付：多年三模式 + 归因报告（含锁死日期修正） |
| 115a4cb | **阶段1-4：81日逐笔配对 + NAV差 A/B 两段分解** |
| c528f96 | LC 挤出机制修正为时序错配实锤 |
| 36d3160 | 本总呈报告 |
| 2784146 | **红线机器验证 redline_audit（R6=TRUE）** |
| dd31a8d / 95dc00d | 旧报告取代声明 + docstring 补状态域说明 |
| a7cf33f | 决策层 RAW 读数（FV 结构负期望 / LC 未充分测量） |
| 04822d3 | **B线根因更正**：代理故障≠落库延迟（误归因完整留痕） |
