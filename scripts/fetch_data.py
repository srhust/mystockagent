# -*- coding: utf-8 -*-
"""数据抓取入口：抓取全部周期板块的指数/个股/估值/商品数据并缓存。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401

from cycle_system import data, universe  # noqa: E402


def main():
    print("=" * 60)
    print(universe.summary_str())
    print("=" * 60)

    imap, missing = universe.validate_universe()
    if missing:
        print(f"[警告] 以下板块的同花顺行业未找到: {missing}")
    else:
        print(f"[OK] 全部 {len(universe.get_universe())} 个板块的同花顺行业映射有效")

    t0 = time.time()
    result = data.load_all()

    ok_idx = sum(1 for k, v in result.items() if k != "__benchmark__" and v.get("index") is not None)
    ok_stk = sum(len(v.get("stocks", {})) for k, v in result.items() if k != "__benchmark__")
    ok_pb = sum(len(v.get("pb", {})) for k, v in result.items() if k != "__benchmark__")
    n_stocks_total = sum(len(m["stocks"]) for m in __import__("config").CYCLE_SECTORS.values())

    print("=" * 60)
    print(f"抓取完成, 耗时 {time.time() - t0:.0f}s")
    print(f"  基准(沪深300): {'OK' if result.get('__benchmark__') is not None else 'FAIL'}")
    print(f"  行业指数: {ok_idx}/{len(data.config.CYCLE_SECTORS)}")
    print(f"  个股行情: {ok_stk}/{n_stocks_total}")
    print(f"  个股PB:   {ok_pb}/{n_stocks_total}")


if __name__ == "__main__":
    main()
