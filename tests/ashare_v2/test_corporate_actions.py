# -*- coding: utf-8 -*-
"""test_corporate_actions — 包D裁定D-3: 企业行动现金/股数守恒测试(合成事件, 机制层)。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "..", "..", "cycle_system", "ashare_v2", "tools"))
from corporate_actions import apply_day_events, audit_availability  # noqa: E402

SRC = "stock_history_dividend_detail:em"


def test_bonus_market_value_continuity():
    """10送4: 理论除权价下市值恒等守恒, 股数按实际比例上调取整+尾股显式。"""
    st = {"cash": 0.0, "positions": {"600000": 100}}
    px_pre = 10.0
    new, ap, uv = apply_day_events(st, [
        {"code": "600000", "type": "BONUS", "ex_date": "2021-05-10",
         "ratio": 0.4, "source": SRC}])
    assert uv == [] and len(ap) == 1
    assert ap[0]["shares_pre"] == 100 and ap[0]["shares_post"] == 140
    px_post = px_pre / 1.4
    assert abs(140 * px_post - 100 * px_pre) < 1e-9      # 市值连续恒等
    assert ap[0]["frac_dropped"] == 0.0


def test_bonus_fraction_explicit():
    """111股10送4 → 155.4 → 155股, 0.4尾股价值损失必须显式记账(不静默)。"""
    new, ap, uv = apply_day_events({"cash": 0.0, "positions": {"000001": 111}}, [
        {"code": "000001", "type": "BONUS", "ex_date": "2021-05-10",
         "ratio": 0.4, "source": SRC}])
    assert ap[0]["shares_post"] == 155
    assert abs(ap[0]["frac_dropped"] - 0.4) < 1e-12


def test_dividend_cash_conservation():
    """现金分红入账: 除息市值降+现金增, 税前总财富守恒; 税代扣显式列示。"""
    st = {"cash": 500.0, "positions": {"600000": 200}}
    px_pre = 8.0
    new, ap, uv = apply_day_events(st, [
        {"code": "600000", "type": "DIVIDEND", "ex_date": "2021-06-01",
         "cash_per_share": 0.5, "tax_rate": 0.15, "source": SRC}])
    assert new["positions"]["600000"] == 200              # 股数不变
    assert abs(new["cash"] - (500.0 + 200 * 0.5 * 0.85)) < 1e-9
    px_ex = px_pre - 0.5                                  # 理论除息
    pre_total = 500.0 + 200 * px_pre                      # 1600
    post_total = new["cash"] + 200 * px_ex                # 1585(税后口径)
    assert abs(post_total - (pre_total - 200 * 0.5 * 0.15)) < 1e-9
    assert ap[0]["tax_withheld"] == round(200 * 0.5 * 0.15, 4)


def test_rights_issue_conservation():
    """配股10配2@5元全缴: 理论除权价恒等 (pre市值+缴款)=post市值。"""
    st = {"cash": 1000.0, "positions": {"600000": 100}}
    new, ap, uv = apply_day_events(st, [
        {"code": "600000", "type": "RIGHTS", "ex_date": "2021-07-01",
         "ratio": 0.2, "rights_price": 5.0, "subscribed_ratio": 1.0, "source": SRC}])
    assert new["positions"]["600000"] == 120
    assert abs(new["cash"] - 900.0) < 1e-9
    px_pre = 10.0
    px_ex = (100 * px_pre + 20 * 5.0) / 120               # 9.1667
    assert abs(120 * px_ex - (100 * px_pre + 20 * 5.0)) < 1e-9


def test_delisting_settle_and_suspend_valuation():
    """退市: 有终价一次出清入账守恒; 无终价保持挂起(SUSPENDED_VALUATION)不入虚假现金。"""
    new, ap, uv = apply_day_events({"cash": 0.0, "positions": {"000001": 100}}, [
        {"code": "000001", "type": "DELIST", "ex_date": "2023-01-20",
         "final_price": 3.0, "source": SRC}])
    assert "000001" not in new["positions"] and abs(new["cash"] - 300.0) < 1e-9
    assert ap[0]["cash_delta"] == 300.0
    new, ap, uv = apply_day_events({"cash": 0.0, "positions": {"000002": 100}}, [
        {"code": "000002", "type": "DELIST", "ex_date": "2023-02-01", "source": SRC}])
    assert ap == [] and uv and "SUSPENDED" in uv[0]["reason"]
    assert new["positions"]["000002"] == 100              # 未动账


def test_untrusted_source_never_applied():
    """来源不在可信登记 → 事件不应用 + 标CORPORATE_ACTION_UNVERIFIED路径(unver)。"""
    new, ap, uv = apply_day_events({"cash": 0.0, "positions": {"600000": 100}}, [
        {"code": "600000", "type": "BONUS", "ex_date": "2021-05-10",
         "ratio": 0.4, "source": "unverified_blog"}],
        registry={"trusted_sources": (SRC,)})
    assert ap == [] and uv[0]["reason"] == "UNTRUSTED_SOURCE"
    assert new["positions"]["600000"] == 100 and new["cash"] == 0.0


def test_mixed_sequence_global_conservation():
    """三券混合事件流: 逐事件理论价重估下Σ(cash+市值)只随分红入账/缴款变动, 误差<0.01。"""
    st = {"cash": 1000.0, "positions": {"A": 100, "B": 300, "C": 50}}
    px = {"A": 10.0, "B": 4.0, "C": 20.0}
    pre_total = st["cash"] + sum(q * px[c] for c, q in st["positions"].items())
    evs = [
        {"code": "A", "type": "BONUS", "ex_date": "d1", "ratio": 0.5, "source": SRC},
        {"code": "B", "type": "DIVIDEND", "ex_date": "d2", "cash_per_share": 0.2,
         "tax_rate": 0.0, "source": SRC},
        {"code": "C", "type": "SPLIT", "ex_date": "d3", "ratio": 1.0, "source": SRC},
        {"code": "Z", "type": "BONUS", "ex_date": "d4", "ratio": 0.4, "source": SRC},
    ]
    new, ap, uv = apply_day_events(st, evs)
    assert uv and uv[0]["reason"] == "NO_POSITION"
    # 理论除权/除息后价: A: 10/1.5, B: 4-0.2, C: 20/2
    px2 = {"A": 10.0 / 1.5, "B": 3.8, "C": 10.0}
    post_total = new["cash"] + sum(q * px2[c] for c, q in new["positions"].items())
    assert abs(post_total - pre_total) < 0.01             # 尾股截断容差
    assert new["positions"] == {"A": 150, "B": 300, "C": 100}
    assert abs(new["cash"] - (1000.0 + 300 * 0.2)) < 1e-9  # 分红只增现金


def test_audit_availability_reports_degraded_not_fabricated():
    d = audit_availability(["600000", "000001"])
    assert d["fetch_status"].startswith("SUSPENDED")
    assert "CORPORATE_ACTION_UNVERIFIED" in d["ledger_marking_rule"]
    assert len(d["interfaces_registered"]) >= 3
