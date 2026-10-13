# -*- coding: utf-8 -*-
"""
周期股股票池：基于 config.CYCLE_SECTORS 的静态精选池（"所有已知周期股"）。

周期股定义特征：行业整体盈利与股价随宏观/产业周期大幅波动。
每个板块包含:
- 同花顺行业指数（周期刻画主体）
- 代表性周期股（板块广度、PB 估值、可交易标的）
- 可选商品期货/美股代理（周期领先指标）
"""
import pandas as pd

import _path  # noqa: F401
import config
from . import data


def validate_universe():
    """校验股票池: 同花顺行业名是否都能找到。返回 (universe_info, missing)。"""
    imap = data.get_ths_industry_map()
    missing = []
    for name, meta in config.CYCLE_SECTORS.items():
        ths = meta["ths"]
        if ths not in imap:
            missing.append((name, ths))
    return imap, missing


def get_universe():
    """返回股票池概览 DataFrame。"""
    rows = []
    for name, meta in config.CYCLE_SECTORS.items():
        rows.append({
            "板块": name,
            "同花顺行业": meta["ths"],
            "周期类别": meta["cat"],
            "周期逻辑": meta["note"],
            "典型周期(年)": meta["cycle_len"],
            "代表股数": len(meta["stocks"]),
            "商品代理": meta.get("fut") or (meta.get("us_proxy") or ""),
        })
    return pd.DataFrame(rows)


def all_stock_codes():
    """所有代表股代码集合（去重）。"""
    seen = {}
    for meta in config.CYCLE_SECTORS.values():
        for code, cname in meta["stocks"]:
            seen[code] = cname
    return seen


def summary_str():
    df = get_universe()
    lines = [f"周期板块股票池: {len(df)} 个板块, {df['代表股数'].sum()} 只代表股(含跨板块重复)"]
    for cat, g in df.groupby("周期类别"):
        lines.append(f"  {cat}: {len(g)} 个板块 -> {', '.join(g['板块'])}")
    return "\n".join(lines)
