# -*- coding: utf-8 -*-
"""可交易载体登记表 v2: sector_id 主键 + 证券代码 + 启用状态。
依据 output/validation_report.md (2026-10-12): 日收益相关>=0.85 为高保真载体。
原则: 行业决定研究方向, 真实证券决定交易收益; 无高保真载体=跳过交易(仅研究观察)。"""

CARRIERS = {
    "broker":    {"sector_name": "证券",     "symbol": "512000.SH", "corr": 0.974, "enabled": True},
    "steel":     {"sector_name": "钢铁",     "symbol": "515210.SH", "corr": 0.967, "enabled": True},
    "coal":      {"sector_name": "煤炭",     "symbol": "515220.SH", "corr": 0.957, "enabled": True},
    "semi":      {"sector_name": "半导体",   "symbol": "512480.SH", "corr": 0.924, "enabled": True},
    "realestate": {"sector_name": "房地产",  "symbol": "512200.SH", "corr": 0.922, "enabled": True},
    "indmetal":  {"sector_name": "工业金属", "symbol": "512400.SH", "corr": 0.883, "enabled": True},
    "farming":   {"sector_name": "养殖",     "symbol": "159865.SZ", "corr": 0.876, "enabled": True},
    "power":     {"sector_name": "电力",     "symbol": "159611.SZ", "corr": 0.869, "enabled": True},
    "minormetal": {"sector_name": "小金属",  "symbol": "159871.SZ", "corr": 0.903, "enabled": True},
}

# 行业别名(兼容 config.CYCLE_SECTORS 键名与全称)
ALIAS = {
    "证券": "broker", "钢铁": "steel", "煤炭": "coal", "煤炭开采加工": "coal",
    "半导体": "semi", "房地产": "realestate", "房地产开发": "realestate",
    "工业金属": "indmetal", "养殖": "farming", "电力": "power", "小金属": "minormetal",
}

# 已知错配(剔极端日 corr<0.85): 只研究不交易
MISMATCH = {
    "玻璃玻纤": "共用建材ETF(年化差30.5pp)", "元件": "载体错配(17.6pp)",
    "农化制品": "载体错配(17.3pp)", "水泥": "载体错配(15.4pp)",
}


def tradable(sector: str) -> bool:
    cid = ALIAS.get(sector)
    return bool(cid and CARRIERS[cid]["enabled"])


def carrier(sector: str):
    """返回 {symbol, corr, sector_id} 或 None。"""
    cid = ALIAS.get(sector)
    if not cid:
        return None
    c = CARRIERS[cid]
    return {"sector_id": cid, "symbol": c["symbol"], "corr": c["corr"]} if c["enabled"] else None
