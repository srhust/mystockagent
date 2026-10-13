# 周期股量化交易系统 — CycleTrend-v1

覆盖周期反转、启动突破、趋势延续、顶部衰退和资金配置的完整周期交易研究系统。
前身：温度引擎 + V4三锚体系（保留为 Baseline）。

## 核心结论（2026-10-13 冻结，全部有同口径实验支撑）

| 问题 | 结论 | 证据 |
|---|---|---|
| 唯一被证实的正增量 | **30/35/35 防御性分批**：单位暴露收益 +50%（97.6% vs 同暴露一次性 65.3%），Sharpe 0.81 | MU 暴露匹配实验（Phase 3） |
| MU 最优配置 | V4 入场 + 30/35/35 + t85 出场：**+10.38%/Sharpe 0.81**，捕获中位 31%，5 次往返 | A-G 消融（Phase 2-3） |
| A 股 ETF 最优配置 | Baseline 六槽一次性：**+5.96%/Sharpe 0.50/MDD -21.4%**（9 只高保真载体，0.1%/边） | ETF 全链路消融 |
| 突破引擎 | **负结果**：裸 Donchian 状态信号 141 次往返绞杀收益；经状态机去重仍捕获中位 0% | Phase 2 |
| 顺势加仓 | **负结果**：突破触发=买局部高点；V4 持仓期间加仓信号零触发 | Phase 2-3 |
| 产业因子 | **负结果+缺失**：DRAM/NAND 免费源不存在；SOX-RS 门控对左侧入场是结构性反作用（MDD -47.7%） | Phase 4 |
| 动态资金分配 | **负结果**：score 加权无收益增量（5.89% vs 5.96%），MDD 恶化，仅恢复更快 | Phase 5 |
| 30 行业漏检诊断 | 49 轮漏检中 42 轮是浅周期（-30%~-40%）；但浅回撤路径的补充入场未通过组合收益验证 | cycle_labels 诊断 |
| 严格滚动 OOS | LR 排序逐折 AUC 0.45-0.80 不稳定；CS+ranker ETF 收益从 5.00%（混入训练分）降至 3.25% | CycleStart-v1 遗产 |

**设计教训**：多路径候选/产业因子/动态分配均未战胜"深寒集中入场 + 防御分批 + 温度出场"。真正的优势来源是入场的极端选择性（17 年 5 次）与分批的风险结构。

## 系统架构

```
cycle_system/
    engine.py               # 唯一交易账本: run_ledger(共享池)/run_specialist(专户/动态预算)
                            #   T+1执行/缺价禁成交/加仓T-1确认T执行/费用入账/暴露度输出
    cycle_labels.py         # 周期标签: 谷底(冻结30%)/zigzag周期/triple-barrier有效启动
    reversal_detector.py    # Engine A 左侧: temp_v4 + 自适应回撤, first-touch标签
    breakout_detector.py    # Engine B 突破: Donchian穿越+回撤前提+失败检测
    cycle_state_machine.py  # 统一状态机: unified(FLAT门控/一轮一cycle_id/防重冷却)
    capital_allocator.py    # 动态资金分配(机会评分+集中度上限+观察预留)
    carriers.py             # A股9只高保真ETF载体(corr≥0.85)
    anchors.py              # 底部/顶部模型拆分
paper/paper_trade.py        # 模拟盘v1.2: ETF真实价/T+1挂单/新鲜度门禁/真实时间戳/账户持久化
tests/test_engine.py        # 7项生产引擎直测(缺价/恒等式/预算封闭/时序/费用)
```

## 复现

```bash
$env:PYTHONIOENCODING='utf-8'
python tests/test_engine.py            # 7/7 引擎测试
python cycle_system/cycle_labels.py    # A股漏检诊断
python scripts/fetch_mu.py             # MU/SPX/SOX 数据
python scripts/mu_cycle_audit.py       # MU 9轮周期审计
python scripts/mu_ablation_ag.py       # A-G消融(状态机门控)
python scripts/mu_phase3.py            # 暴露匹配
python scripts/etf_ablation_cs.py      # A股ETF全链路消融(严格OOS)
python scripts/capital_allocation_test.py  # 动态分配对照
python paper/paper_trade.py            # 每日收盘后模拟盘
```

数据口径：A股行业指数(akshare, 2010起)生成信号；执行=A股ETF前复权收盘(qfq)；美股MU前复权；
成本 0.1%/边；信号 T 日收盘确认、T+1 执行；所有阈值冻结(12/75/85/30%/35%/35%)，参数来源为设计起点而非优化结果。

## 历史版本

- V4三锚/30-35-35专户的旧口径结果（单笔收益+58-65%等）为**已完成交易单笔统计**，不等价于账户 CAGR，
  同口径迁移后专户结构收益 3.2-3.7%（见 `output/v4_spec_etf.txt`）——保留作历史研究记录。
- CycleStart-v1 三路径+LR 排序：保留于 `reversal_detector.py`/`cycle_ranker.py`，研究轨。

## 未解决问题

1. 浅回撤启动的召回缺口（42 轮漏检）尚无可盈利的入场方案
2. 真实产业数据（DRAM/NAND/库存）需付费源；当前产业层仅有 SOX 价格代理
3. 顶部模型（anchors.top_score）未通过退出规则独立验证，未接入模拟盘
4. 2010-2026 区间已被反复研究，最终结果不能声称完全独立样本外；模拟盘前向记录才是最终检验
