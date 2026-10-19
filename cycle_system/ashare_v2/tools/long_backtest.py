# -*- coding: utf-8 -*-
"""long_backtest — A线历史回测评估(人类裁定2026-10-19): 全历史多年REPLAY。

冻结数据(R31面板2020→2026-10-16), 零策略参数改动(Runner/引擎/执行器全冻结口径),
window仅放宽时间范围。输出 output/ashare_v2/long_backtest/(不覆盖81日shadow_run证据)。

用法: python -X utf8 cycle_system/ashare_v2/tools/long_backtest.py [start] [end]
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from cycle_system.ashare_v2 import _path                                   # noqa: F401,E402
from cycle_system.ashare_v2 import config as C                              # noqa: E402
from cycle_system.ashare_v2 import report as RP                             # noqa: E402
from cycle_system.ashare_v2.data_portal import DataPortal                   # noqa: E402
from cycle_system.ashare_v2.runner import Runner                            # noqa: E402
from cycle_system.ashare_v2.schemas import RunMode                          # noqa: E402

OUT = os.path.join(C.OUTPUT_DIR, "long_backtest")


def main() -> int:
    start = sys.argv[1] if len(sys.argv) > 1 else "2020-06-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-10-16"
    portal = DataPortal(data_mode=C.PIT_RESEARCH)          # 冻结校验照常
    r = Runner(RunMode.SHADOW.value, portal=portal, window=(start, end),
               ledger_root=os.path.join(OUT, "ledger"), ledger_name="dual_cycle_v2_long")
    res = r.run()
    files = RP.write_all(OUT, r, res)
    cnt = {}
    for x in r.coord.execution_log:
        for j in x["rejects"]:
            cnt[j["reason"]] = cnt.get(j["reason"], 0) + 1
    slim = {k: res[k] for k in ("mode", "window", "days_simulated", "nav_final", "cash_final",
                                "conservation_ok", "virtual_real_reconciled", "n_fills", "n_rejects")}
    print(json.dumps({"mode": "SHADOW_LONG", "files": files, **slim, "reject_top": cnt,
                      "notes": "REPLAY/RESEARCH口径: 历史回放非前向样本, 不构成预测有效性宣称"},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
