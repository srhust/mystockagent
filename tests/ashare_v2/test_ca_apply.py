# -*- coding: utf-8 -*-
"""test_ca_apply — R1_CAI 入账语义合成测试(CAI.0-PREREG 逐条对应)。"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                                "cycle_system", "ashare_v2", "tools")))
from ca_apply import apply_bonus, apply_dividend, tax_tier_after  # noqa: E402

EV = {"code": "600000", "type": "DIVIDEND", "ex_date": "2026-03-01",
      "cash_per_share": 0.1, "cash_after_tax_options": [0.09, 0.1],
      "plan_text": "10派1.0(含税)"}


def test_tier3_rules():
    assert tax_tier_after(0.1, 400, [0.09, 0.1]) == (0.1, "EXEMPT_GT365", False)
    assert tax_tier_after(0.1, 100, [0.09, 0.1]) == (0.09, "TAX10_30_365", False)
    assert tax_tier_after(0.1, 10, [0.09, 0.1]) == (0.08, "TAX20_LT30", False)
    assert tax_tier_after(0.1, 30, [0.09, 0.1])[1] == "TAX10_30_365"      # 边界30入10%
    assert tax_tier_after(0.1, 365, [0.09, 0.1])[1] == "TAX10_30_365"     # 365仍10%(>365免税)


def test_tier_mismatch_not_silent():
    after, tier, mm = tax_tier_after(0.1, 100, [0.05, 0.06])              # options与规则档不符
    assert tier == "TAX10_30_365" and mm is True                           # 仍按规则档, 打旗标


def test_dividend_multi_lot_fifo_tiers():
    lots = [{"shares": 100, "entry_day": "2020-01-01"},                      # >365 免税
            {"shares": 200, "entry_day": "2026-01-01"}]                      # ~59天 10%
    cash, rows = apply_dividend(0.0, lots, EV, "2026-02-27")
    amts = [r["amount"] for r in rows if r["tax_chosen"] not in ("TAX_OPTION_MISMATCH",)]
    assert round(sum(amts), 2) == round(100 * 0.1 + 200 * 0.09, 2) == 28.0
    assert cash == 28.0
    assert [r["tax_chosen"] for r in rows] == ["EXEMPT_GT365", "TAX10_30_365"]


def test_no_holder_row_only():
    cash, rows = apply_dividend(500.0, [], EV, "2026-02-27")
    assert cash == 500.0 and rows[0]["tax_chosen"] == "NO_HOLDER"


def test_audit_equals_cash_delta(CAI5=1):
    lots = [{"shares": 300, "entry_day": "2026-02-01"}]                       # <30天 20%
    cash, rows = apply_dividend(1000.0, lots, EV, "2026-02-27")
    assert round(cash - 1000.0, 2) == round(sum(r["amount"] for r in rows), 2) == 24.0


def test_bonus_shares_and_cost_dilution():
    total, lots, px, rows = apply_bonus(100, [{"shares": 100, "entry_day": "2026-01-05"}],
                                        {"p1": 10.0},
                                        {"code": "600000", "type": "BONUS", "ex_date": "2026-03-01",
                                         "ratio": 0.5, "plan_text": "10转5"}, factor_ratio=1.5)
    assert total == 150 and lots[0]["shares"] == 150 and round(px["p1"], 4) == 6.6667
    assert abs(100 * 10.0 - 150 * px["p1"]) < 0.01                           # 市值守恒(除权前成本轴)
    assert rows[0]["amount"] == 50                                           # 股差审计行


def test_bonus_factor_inconsistent_flag():
    _, _, _, rows = apply_bonus(100, [], {}, {"code": "x", "type": "BONUS",
                                              "ex_date": "2026-03-01", "ratio": 0.5},
                                factor_ratio=1.2)                            # 面板比率与事件差>1%
    assert any(r["tax_chosen"] == "FACTOR_INCONSISTENT" for r in rows)
