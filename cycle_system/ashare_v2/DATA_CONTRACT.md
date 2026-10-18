# Ashare Dual-Cycle V2 — 数据契约 (阶段A定死, 阶段B实现)

> 本契约定义 V2 允许读取的每一个数据集、每条记录的强制字段、以及"PIT 可知"判定规则。
> 任何实现偏离本契约 = 缺陷, 必须显式标注, 不得静默。缺数据 → `DATA_BLOCKED`, 不编造。

## 1. 价格与日历(现有锚定数据, 只读复用)

| 数据集 | 位置 | 键 | 强制字段 | 备注 |
|---|---|---|---|---|
| 日线CSV | `data/stocks/{code}.csv` | (date, code) | date/open/close/high/low/volume/amount/turn/pctChg/peTTM/pbMRQ/isST*/adj_factor | 250 文件; sha256 以 `output/ashare/manifest.json` 为锚; 每阶段用 `tools/baseline_integrity.py` 重验 |
| 市场日历 | `data/ashare_calendar.csv` | date | trade day + 节假日交叉校验 | 复用 `ashare_calendar.py`; 未知日期 fail-closed |
| 面板缓存 | `output/ashare/_panel_cache.parquet` | — | 派生缓存 | V2 不依赖; 依赖仅限 CSV 重算 |

已知缺陷(显式标注, 不静默): `st_history=UNAVAILABLE`(历史 ST 状态不可得)、
`suspension=行情近似`(以缺行近似停牌)、isST 列当日口径不可靠(swing_m1 传 `is_st=False`,
V2 同样标 `ST_FLAG_UNVERIFIED`, 不假称已处理)。

## 2. 历史股票池(§7.1)

来源: `output/ashare/stock_universe_full.json`(Baostock query_stock_basic 全名录,
ipo/out/type/status 全字段, R46 落盘+R48 规范化哈希)与 `stock_universe_rebuild.json`(逐月全集曲线)。

分层(bucket, 由 `universe.py` 输出):
`LISTED_BEFORE(T) → SURVIVING(T) → HAS_QUOTES(T) → TRADEABLE(T)(复用 pool_at) →
ST_FLAG(T)(UNVERIFIED) → LIQUIDITY(T) → DATA_ENOUGH(T)`

红线: 当前 250 配置池 ≠ 历史池; 使用当前池回测必须标
`SURVIVORSHIP_BIAS_PARTIAL`; 禁止宣称已消除幸存者偏差。
Baostock 服务不可达时: 用已落盘名录, 不得新拉取回填; 状态 `DATA_BLOCKED`(新增数据)。

## 3. 行业成员与指数(§7.2)

`IndustryMembershipProvider` 返回 `(行业, 分类版本, trust, asof)`; trust 枚举(强制字段):

| trust | 含义 | 允许的用途 |
|---|---|---|
| `SW_PIT_VERIFIED` | 申万历史成员按时点可证 | 正式信号 |
| `SW_PARTIAL` | 申万但部分时段缺失/修订不一致 | 受限研究, 逐日标注 |
| `CSRC_HISTORICAL_DATE` | 证监会历史时点可查 | 独立口径分析, 不得冒充申万 |
| `CURRENT_SNAPSHOT_ONLY` | 仅当前快照(如 `industry_member_map*.json`) | 代理研究, `PROXY_ONLY` 标注 |
| `UNKNOWN` | 无可用时点信息 | 不出行业级信号 |

当前实际状态(2026-10-18): 申万二级历史成员归属 = `UNKNOWN`; 证监会代码白名单口径 =
`CSRC_HISTORICAL_DATE`(R48); 个股池 sector 列 = `CURRENT_SNAPSHOT_ONLY`。
→ Strict 模式行业过滤信号 `DATA_BLOCKED`; Research 模式允许快照代理但逐条标注。
2014/2021 分类版本必须显式区分; 语义不同的行业指数禁止拼接(跨版本须断点重基或分段)。

## 4. 基本面与周期指标(§7.3, 适配器契约)

每条指标记录强制字段(全部必填, 缺任何一项 → 该记录不可进入策略, 只可进入研究诊断):

```
indicator_id   指标唯一名(如 pig.sow_stock, cu.tc_rc)
industry       行业 + 分类版本
period         所属期间(月/季/年, 及起止日)
value          原始值(单位随附)
source         来源(机构名+接口名)
first_public_ts    首次公开时间(ISO8601 含时区)
effective_ts       生效时间
fetched_ts         本系统获取时间
revision           修订版本(0=首版)
completeness       完整度标注(COMPLETE/PARTIAL/PROXY)
credibility        CONFIRMED / RECONSTRUCTABLE / NOT_VERIFIED
```

**PIT 闸门规则**: 指标可用于信号日 D ⇔ `first_public_ts ≤ D 收盘`(若 first_public_ts 缺失 →
`UNKNOWN`, 不可用, 不回填)。季度利润 ≠ 季度末当天可知。

各行业适配器(可插拔, 未接入=显式 `DATA_BLOCKED`, 不得静默缺省):
猪(猪价/能繁母猪/成本/利润) 铜(供需/库存/TC-RC/价格/产能) 金(实际利率/金价/产量/成本)
煤(价/库存/产量/需求/盈利与资本开支) 工程机械(销量/开工/更新/库存/盈利)
半导体(库存/订单/价格/资本开支/盈利) 银行(净息差/信贷/资产质量) 证券(成交/两融/投行/盈利)
保险(利率/权益/投资收益) + 成长景气(独立成长股适配器, 不套商品周期)。

## 5. 数据不足行为(§7.4)

- **Strict**: 关键时点信息缺失 → 拒绝正式交易信号, 产出 `DATA_BLOCKED` 明细(哪个字段/哪个时点)。
- **Research**: 允许价格代理, 输出 `PROXY_ONLY / DATA_LIMITED / UNKNOWN`, 归类"受限研究",
  不得包装成已验证策略。
- 禁止: 用今天快照静默填补历史映射; 用外部数据不可得之名编造数值。

## 6. 产出溯源(每份 JSON 强制头块)

```json
{"_provenance": {"code_version": "<git_sha+dirty标记>", "branch": "ashare-v2",
  "config_hash": "<sha256(config.py序列化)>", "data_hashes": {"stocks_manifest": "<sha>",
  "calendar": "<sha>"}, "asof": "<信号日>", "generated_at": "<ISO8601>",
  "pit_notes": ["<本产物涉及的显式标注>"], "mode": "DIAGNOSTIC|SYNTHETIC|SHADOW"}}
```

## 7. 复用与隔离

复用(只读 import, 不修改): `ashare_calendar`、`ashare_tradeable_pool.pool_at`、
`ashare_executor`(create_plan/execute_plan/Ledger)、`ashare_stock_pipeline.configured_stocks`(池定义)。
隔离(必须新建, 不复用旧语义): 周期状态判定、行业阶段过滤(信任分层)、候选生成、评分、
分配、协调。`data_portal` 是唯一允许触达外部新数据源的出口, 且每次拉取须记录 fetched_ts+响应哈希。
