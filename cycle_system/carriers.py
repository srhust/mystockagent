# -*- coding: utf-8 -*-
"""可交易载体登记表: 行业信号与ETF载体分离。
依据 output/validation_report.md (2026-10-12 修复后): 日收益相关>=0.85 为高保真载体。
原则: 行业决定研究方向, 真实证券决定交易收益; 无高保真载体=跳过交易(仅研究观察)。"""

# corr>=0.85 的行业→ETF 映射(唯一载体, 不允许多行业共用一条高保真映射)
QUALIFIED = {
    "证券": "券商ETF",        # 0.974
    "钢铁": "钢铁ETF",        # 0.967
    "煤炭开采加工": "煤炭ETF",  # 0.957
    "半导体": "半导体ETF",     # 0.924
    "房地产开发": "房地产ETF",  # 0.922
    "工业金属": "工业金属ETF",  # 0.883
    "养殖": "养殖ETF",        # 0.876
    "电力": "电力ETF",        # 0.869
    "小金属": "小金属ETF",      # 0.903
}

# 已知错配(剔极端日 corr<0.85): 只研究不交易, 或等待定制篮子
MISMATCH = {
    "玻璃玻纤": "共用建材ETF(年化差30.5pp)",
    "元件": "载体错配(17.6pp)",
    "农化制品": "载体错配(17.3pp)",
    "水泥": "载体错配(15.4pp)",
}


def tradable(sector: str) -> bool:
    return sector in QUALIFIED


def carrier(sector: str) -> str | None:
    return QUALIFIED.get(sector)
