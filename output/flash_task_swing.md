# 任务书：波段买卖点层回测（scripts/backtest_swing.py）

> 交给执行代理（glm-5.3-flash / deepseek-v4.1-flash）。任务书自包含。
> **诚实报告要求：只报告代码里实际实现的功能与真实运行产出的数字，父代理会逐行审查代码并重新运行验证所有数字。**

## 背景

系统位于 E:\mystockagent（Windows，Python 3.9）。周期策略核心（cold 模式）：板块温度 temp∈[0,100]（`data/enriched.pkl` 每板块 df 含 temp/stage/close/ma20/ma60/ma200/mom60/price_pos/pb_pct 等列，DatetimeIndex 至 2026-10-09），temp<12 买入、>75 卖出，6 仓等权，同类≤2，T 日收盘信号→T 日收盘价成交，单边成本 0.15%。回测引擎 `cycle_system/backtest.py::run_backtest(dfs, meta, mode, cost_rate, start, end, ...)` 返回 {equity, weights, trades, metrics}。

现有策略是"一档全进全出"，持有期对波动零响应。本任务实现并验证**四个独立波段变体**（每个单独叠加在基线上对比，隔离归因）。

## 代码组织

新建 `scripts/backtest_swing.py`（**不得改动 cycle_system/ 任何文件**）。头部照抄 scripts/backtest_etf.py 的模式：`sys.path.insert` + `import _path` + `warnings.filterwarnings("ignore", category=FutureWarning)` + matplotlib Agg + `config.PLOT_FONTS` 中文字体。

**模拟器**：自己写逐日循环（参考 scripts/backtest_etf.py 的 simulate_etf 的组合规则实现——卖出优先、空位当日回补、strength 排序、同类≤2、成本=换手×0.15%），但信号与成交都用**指数数据**（不用 ETF）。基线模拟器必须先复现 run_backtest 的 cold 结果（±0.5pp 内），再叠加波段变体。

## 规格定义（严格按此实现，参数走 CLI 可调）

### 主仓（所有变体共用，与基线 cold 一致）
- 目标仓位：temp<12 → 该板块进入候选；temp>75 → 清仓（迟滞状态机直接用 `strategy.position_target(df, mode="cold")` 的输出）
- 组合：最多 6 仓等权（每仓 1/6），同类（config.CYCLE_SECTORS[name]["cat"]）≤2，候选按 `strategy.signal_strength` 降序
- 阶段列：df["stage"]（字符串：decline/bottom/recovery/expansion/topping）

### 变体 S-A 分批兑现（卖点波段化）
- 持仓板块 temp 首次 >65：卖出该仓位的 1/3；temp >75：再卖 1/3；temp >85：清仓
- 对照基线：temp>75 一次性全卖
- 实现为该板块目标权重从 1/6 → 1/9 → 1/18 → 0 的阶梯

### 变体 S-B 乖离减仓（持有期波段卖点）
- 条件：持仓板块 stage∈{expansion, topping} 且 (close/ma20 - 1) > 0.15 → 该板块权重 × 0.5（现金留存）
- 恢复：乖离回落 < 0.05 → 恢复至 1.0 倍标准仓
- 迟滞：单板块减仓/恢复状态切换后 10 个交易日内不再反向

### 变体 S-C 回调加仓（持有期波段买点）
- 条件：持仓板块 stage∈{recovery, expansion} 且 35≤temp≤70 且 (close/ma60 - 1) ≤ -0.03 且 mom60>0 → 该板块加仓 +0.5 份（组合允许到 110%，即微杠杆上限 1.1）
- 回收：乖离回到 > +0.05 或 temp>75 → 卖出波段加仓部分（只卖加仓部分，主仓不动）
- 每板块同一波加仓只做一次（加仓回收后条件再次满足可再触发）

### 变体 S-D 两段式建仓（反转仓位波段化）
- temp<12 → 建 0.6 份标准仓（而非 1.0）
- 加满条件（满足其一，30 日窗口内有效）：
  a) temp 上穿 40（启动确认）→ 补到 1.0
  b) temp < 8 且 close 创 20 日新低 → 补到 1.0（左侧补满）
- 30 日窗口过后未触发 → 补到 1.0（默认补满，防止长期低配）
- 清仓规则与基线一致（temp>75）

### 运行矩阵（6 组）
1. 基线（复现 run_backtest cold）
2. 基线+S-A　3. 基线+S-B　4. 基线+S-C　5. 基线+S-D　6. 基线+S-A+S-B+S-C+S-D 全叠加

每组 × 两个窗口：2010-01-01~末尾、2017-01-01~末尾（重点看 2017-18 磨底段表现）。成本 0.15%。

## 产物

- 终端：6 组 × 2 窗口指标表（年化/MDD/Sharpe/笔数/换手/相对基线年化增量）
- `output/swing_compare.md`：指标表 + 分解（每组相对基线的增量归因：加仓部分收益/减仓规避损失）+ 各波段动作次数统计（S-A 分批卖次数、S-B 减仓次数与平均持有时长、S-C 加仓次数与胜率、S-D 两段触发比例）+ 结论（哪些变体有效、哪些无效，用数字说话）
- `output/swing_curves.png`：6 组净值曲线（2017 起窗口）
- 重点回答：**2017-18 磨底段，S-D 两段式建仓是否减少了亏损？**（基线该段 -17.6%）

## 完成标准

- py_compile 通过；基线模拟器与 run_backtest 对齐验证（打印两者年化对比）
- 全部 6×2=12 组真实运行完成，数字来自真实运行
- 回报：基线对齐精度 + 12 组指标表 + 各变体动作统计 + 3 条以内数据支撑的结论 + 未实现/偏离规格之处（如有）
