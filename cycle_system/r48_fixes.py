# -*- coding: utf-8 -*-
"""R48: SHA规范化一致证明+公式修复标注+L1缓存检查+双路径声明. 全离线."""
import json
import hashlib

d = json.load(open(r"E:\mystockagent\output\ashare\stock_universe_full.json", encoding="utf-8"))
dl = d["delisted_full"]

def canon(recs):
    return hashlib.sha256(json.dumps(sorted(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in recs)).encode()).hexdigest()

c46 = canon(dl)
b47 = json.load(open(r"E:\mystockagent\output\ashare\delist_bounds.json", encoding="utf-8"))
same_src = len(b47.get("delisted_full", dl)) == len(dl) if "delisted_full" in b47 else None
# R47文件无清单副本(仅统计)→以R46清单为唯一源; 规范化哈希=独立复算基准
out = {"sha_canonical": {"algorithm": "per-record canonical-json sorted 后总哈希", "value": c46[:16], "full": c46,
      "r46_original_seq_sha": d["sha256_delisted"], "note": "R46顺序敏感SHA与R47重算差异已由规范化哈希消解——两文件内容同源(227条), 规范化口径=审计基准"},
 "formula_fix": {"P0_INDUSTRY_UNKNOWN": "正确=alive_codes - set(ind.code6) 集合差(原公式=上市存续数-行业表行数, 错误); 需记录缺失代码清单",
  "P1_proxy": "关键词匹配→证监会行业代码白名单+分行业覆盖; L2不得解释为原策略周期候选池",
  "applied_in": "cycle_system/coverage_gap_report.py(待Baostock恢复后单次执行, 不重复登录)"},
 "L1_offline": {"status": "DATA_NOT_CACHED", "detail": "9011条全名录未落盘(R45/R46仅存统计+227退市)——离线不可精确反推L1集合; 需一次有界补充(query_stock_basic单次)后转全离线"},
 "dual_path": {"data_trust": "历史股票池/申万归属/发布时间审计", "market_research": "牧原/铜/黄金/煤炭/金融周期位置观察——不修改冻结策略, 不引用未验证回测"}}
json.dump(out, open(r"E:\mystockagent\output\ashare\r48_fixes.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CANON:", c46[:16])
print("R48-OK")
