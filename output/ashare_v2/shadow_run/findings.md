# Ashare Dual-Cycle V2 findings — mode=SHADOW

- 窗口: ('2026-03-02', '2026-06-30')  模拟天数: 81
- 代码版本: d027ecc74084738ed8afdeb16f2523d3fd5358da  配置哈希: cc5cb7c990c2a499
- 数据锚: manifest 90dd925b61c4…(250/250)

## 四态分离
- 编译/静态: PASS
- 工程测试: PASS(pytest tests/ashare_v2, 见 experiment_manifest)
- 数据同步: PASS(R31锚一致, baseline_integrity C1-C7)
- **预测有效: NOT_CLAIMED —— 全流程未经独立样本外验证, 不声明任何预测有效**

## 显式标注
- SURVIVORSHIP_BIAS_PARTIAL(池=当前配置)
- 行业信任=SW_PARTIAL/CURRENT_SNAPSHOT_ONLY(PROXY), Strict无正式信号
- 评分未校准(CALIBRATED=False): 禁止预期收益率/胜率解读
- ST历史不可得; 停牌=缺行近似
- 2020-2026研究窗非样本外
- 模式边界: SHADOW

## 运行读数(工程口径, 非绩效宣称)
- 候选(合并后)记录数: 5537
- 成交笔数: 32  执行拒绝: 8  拒绝分布: {'insufficient_cash_after_fees': 8}
- 守恒: nav=cash+Σ持仓 OK; 虚拟=真实对账 OK
- NAV(模拟账本, 基20000): 19984.86 → 17565.15(仅系统守恒性读数, 不含滑点建模全量, 禁止绩效解读)
- 风险预算事件: 16
- 协调器显式标注: 无

## 失败项清单
- (逐日拒绝原因见 allocation_plan_log.csv / trades.csv REJECT_* 行)
