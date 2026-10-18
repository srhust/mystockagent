# -*- coding: utf-8 -*-
"""R37a: 历史成员数据源可得性调查(只读, 无凭据不制造VERIFIED) + R36口径修正."""
import json

out = {
    "r36_caliber_corrections": {
        "cand_log_406725": "候选处理事件数(非唯一候选数——同一股同日可有前置筛选+后置判定双记录); 覆盖率计算须按阶段分层取分母",
        "index_audit": "季度末抽样(freq=QE)非逐日时点验证",
        "2020_2021_index": "PRE_TAX 1488/1743=85.4%(含其他UNKNOWN为86.9%, 原提交87%表述过强)",
    },
    "sources": {
        "tushare_index_member_all": {"status": "DOC_CONFIRMED", "fields": ["ts_code", "l1/l2/l3_code", "in_date", "out_date", "is_new"], "requirement": "2000积分; 无凭据(环境核查), 采购决策待依赖链评估后", "must_answer": ["2014版成员可查性", "2021版退出成员(is_new=N)完整性", "in/out_date区间无重叠缺口"]},
        "swsresearch_official": {"status": "CHANNEL_FOUND", "evidence": "官网'指数公告'栏目存在成分调整公告实例(申万50 2020-12-28调整公告); 行业指数(801xxx)公告覆盖度待R37b逐期只读抓取验证", "next": "R37b: 行业指数公告列表抓取→历史调整名单可得性→与AKShare纳入日期交叉核对"},
        "akshare": {"status": "AUX_ONLY", "role": "2021版现存成分+纳入日期辅助交叉检查; 不作VERIFIED依据"},
    },
    "acceptance_5": ["2014/2021版体系标识明确", "历史退出成员可查询", "in/out_date区间合理无重叠缺口", "冻结30样本x4时点可判VERIFIED/PARTIAL/CONFLICT/UNKNOWN", "原始来源+查询日期+记录哈希+接口版本完整留存"],
    "discipline": "无凭据阶段: 只做接口能力与文件可得性调查; 不猜测/不回填/不用当前映射制造VERIFIED",
    "boundary": "工程可复现(已证)≠信息时点正确(待真实历史成员数据)≠样本外预测有效(未证)",
}
json.dump(out, open(r"E:\mystockagent\output\ashare\history_source_survey.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("R37A-OK")
