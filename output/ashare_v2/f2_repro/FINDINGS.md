# F2 复现 P0 — 解决记录 (阶段A, 2026-10-18)

## 结论
**F2_REPRO_RESOLVED** — `output/ashare/f2_repro_p0.json` (OPEN_P0) 由本记录解决。
V2 对照基线标注: **BASELINE_REPRODUCED**（冻结数字当前可复现, 参数集已恢复并独立验证）。

## 参数集恢复来源(两处独立互证)
1. `cycle_system/swing_flow_audit.py` L10 (R30/R31 入库, commit a304606 时代即存在):
   `run("expt", gate=True, exit_mode="monthly", entry_mode="daily", cap=0,
   alloc_policy="score", risk_mode="gate", gate_realloc="realloc")`
2. `output/ashare/manifest.json → candidates.F2_gate_realloc` (R31 账本): 同一参数集,
   并补全 `s2_bad_win=20, s2_rs_th=0.0`(run 默认值, 显式确认)。

616fc0d 当时尝试的是"默认参数"与"仅 risk_mode=gate"两种组合, 未用完整组合——P0 的成因是
**参数集检索不完整**, 而非账本或数据变化。

## 证据链(独立三层)
| 层 | 证据 | 结果 |
|---|---|---|
| 数据 | 250/250 CSV sha256 ↔ R31 manifest (C1, 本阶段重验) | 一致 |
| 磁盘旁证 | 2026-10-18 11:57 一次重跑: F2_realloc_full.json 被重写且 git 判与 HEAD **逐字节一致**; 同秒 cap0 侧效文件统计=冻结账本 (phaseA_evidence/cap0_20261018T1157_stats_extract.json) | 一致但未留档参数 |
| 本会话独立重放 | `tools/f2_repro_check.py`(HEAD 代码, 第三次重放) 与冻结账本 15 字段逐字段比对: return 0.4964 / dd -0.3515 / s2gate 1253 / BUY 163 / SELL 154 … | **零差异**, 判定见 f2_repro_resolution.json |

重放过程中 F2 冻结产物自身哈希前后不变(工具不写冻结路径); `swing_m1.run()` 的侧效文件
`swing_m1_expt_True_monthly_3f_daily_cap0.json`(非官方冻结产物)已由维护者 git restore 复位。

## 代码锚点漂移的定性(C7, 显式标注)
- manifest.code_hashes 锚定 `swing_m1.py` R30 磁盘态(8a5cb054…)；当前 HEAD 为 bceb15cb…
- 逐提交字节比对定位: 差异仅来自 **R35(5b6d812) 与 R36(c464c51)** 两次提交, 二者自述为
  "只读候选审计日志(cand_log), 不改执行"。
- 铁证: 以上述当前 HEAD 代码重放 F2 参数集 → 与 R30 期冻结账本逐字段一致 ⇒
  **执行语义未漂移**；"run() 语义演进导致不可复现"假说被本次重放排除。
- 其余三文件(executor/tradeable_pool/stock_pipeline)与锚完全一致。
- 环境注记: `core.autocrlf=true` 且无 .gitattributes —— 磁盘哈希依赖 checkout 行尾态；
  阶段A起 V2 以 **git-blob 哈希 + CRLF 归一化** 双口径记录, 避免该脆弱性(见 C2/C5/C7 实现)。

## 四态边界声明
本次解决的是"工程可复现"态; 不改变: F2 数字仍是 2020–2026 研究窗口内探索性结果
(非样本外绩效, 见产物内 caveats), V2 不得据此宣称预测有效。
