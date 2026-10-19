# Ashare Dual-Cycle V2 阶段G统一验收

- 版本: f1a322f64fd55b8a87bc516a9f085c4db7ea2531 (ashare-v2)  配置哈希: cc5cb7c990c2a499

| 验收项 | 结果 |
|---|---|
| V2工程测试 tests/ashare_v2 | 87 passed, 0 failed |
| 全量回归 tests/ | 135 passed, 0 failed |
| 基线完整性 C1-C7 | PASS ({"C1_csv_manifest": "PASS", "C6_tamper_selftest": "PASS", "C2_frozen_artifacts": "PASS", "C3_us_frozen": "PASS", "C4_worktree_frozen": "PASS", "C5_swing_m1_untouched": "PASS", "C7_frozen_code_hashes": "PASS_ANNOTATED"}) |
| F2复现P0 | F2_REPRO_RESOLVED |
| 冻结文件在V2分支被改动 | 无 |
| 固定引擎仓位/持仓数禁用 | True |
| LC退出无天数条款 | {'long_cycle_no_held_days': True, 'lc_exit_branch_no_held_days': True} |
| 产物收益话术违规 | 0 |

## 失败/阻塞显式清单
- SW历史成员in_date无out_date→信任层封顶SW_PARTIAL; 行业指数PROXY_ONLY; STRICT模式无正式信号(闸门由test_phaseC锁)
- F2复现P0: 第三次独立重放8字段全一致→F2_REPRO_RESOLVED(f2_repro_resolution.json); 历史f2_repro_p0.json原件保留不改作为过程证据, 未静默绕过
- 9011只股票六重过滤原始行未落盘→市场全域统计DATA_BLOCKED(universe显式标注)
- 603603 sector为空→行业特征缺失即不参选(非静默)
- C7代码锚=PASS_ANNOTATED: B0/B1产物生成后执行器/管线有已授权修复提交, 哈希漂移显式标注而非伪PASS
- ST历史标记不可得; 停牌=缺行近似(执行器按halted参数)
- swap已执行化(验收4): allocator冻结判据批准后E层卖先于买执行, 单名/组合/行业三上限同源复验(含当日buys), 撤销/跳过一律落rejects(验收6)
- 前向影子R0: FREEZE锚=2026-10-16(HEAD 8857398), 信号日>锚=FORWARD; STRICT四闸门未解除⇒仅行为验证准入, 非正式信号
- 评分权重/阈值全部为未校准预设(CALIBRATED=False), 2020-2026非样本外→不声明预测有效

## 四态分离终判
- 编译: PASS
- 工程测试: PASS(含§9.1+§9.2 A-H合成场景)
- 数据同步: PASS(250/250 CSV锚一致)
- **预测有效: NOT_CLAIMED —— 未经独立样本外验证, 本项目不声明预测有效**

## 验收结论: PASS_WITH_ANNOTATIONS