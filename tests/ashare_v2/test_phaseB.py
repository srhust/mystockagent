# -*- coding: utf-8 -*-
"""阶段B工程测试(§9.1 适用项): 无未来信息泄漏 / 产业指标晚于信号日不可见 /
无数据 Fail-Closed / 行业状态受限标注 / 历史池分层边界。"""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from cycle_system.ashare_v2 import config as C                    # noqa: E402
from cycle_system.ashare_v2 import universe as U                  # noqa: E402
from cycle_system.ashare_v2.data_portal import (DataPortal, DataIntegrityError,  # noqa: E402
                                                 INDICATOR_CATALOG)
from cycle_system.ashare_v2.industry_cycle import (IndustryMembershipProvider,   # noqa: E402
                                                   IndustrySeriesBuilder, stage_features, classify_state)
from cycle_system.ashare_v2.schemas import Availability, LongCycleState, MembershipTrust  # noqa: E402


@pytest.fixture(scope="module")
def portal():
    return DataPortal(data_mode=C.PIT_RESEARCH, verify_hashes=True)


# ── Fail-Closed ────────────────────────────────────────────
def test_strict_mode_rejects_unverified():
    p = DataPortal(data_mode=C.PIT_STRICT, verify_hashes=False)
    with pytest.raises(DataIntegrityError):
        p.price_panel()


def test_hash_mismatch_fail_closed(tmp_path, monkeypatch):
    man = json.load(open(os.path.join(ROOT, "output", "ashare", "manifest.json"), encoding="utf-8"))
    name = sorted(man["csv_hashes"])[0]
    (tmp_path / name).write_bytes(b"date,open\n2020-01-02,1.0\n")
    p = DataPortal()
    monkeypatch.setattr(C, "STOCKS_DIR", str(tmp_path))
    with pytest.raises(DataIntegrityError):
        p.price_panel()


def test_calendar_unknown_day_fail_closed(portal):
    with pytest.raises(RuntimeError):
        portal.is_trading_day("2027-06-01")


# ── 面板构建 ───────────────────────────────────────────────
def test_panel_integrity(portal):
    import pandas as pd
    df = portal.price_panel()
    assert df[["date", "code"]].duplicated().sum() == 0
    assert {"close_adj", "open", "amount", "sector"} <= set(df.columns)
    assert df["date"].min() >= pd.Timestamp("2020-01-02")
    codes = set(df["code"])
    assert "603603" in codes                      # 额外CSV保留
    assert len(codes) in (249, 250)               # 249唯一池(+603603)
    assert df.loc[df["code"] == "603603", "sector"].eq("").all()  # 行业层排除并标注

# ── 基本面 PIT 闸门(§9.1 产业指标晚于信号日不可见) ─────────
def test_fundamental_invisible_before_publication():
    p = DataPortal()
    rec = {"indicator_id": "pig.sow_stock", "industry": "生猪养殖", "period": "2025-Q4",
           "value": 41000, "source": "MOA.test", "first_public_ts": "2026-03-15T10:00:00+08:00",
           "effective_ts": "2025-12-31", "fetched_ts": "2026-03-16T09:00:00+08:00",
           "revision": 0, "completeness": "COMPLETE"}
    p.register_fundamental(rec)
    assert p.pit_records("pig.sow_stock", "2026-03-14")["records"] == []     # 公开前不可见
    got = p.pit_records("pig.sow_stock", "2026-03-16")["records"]
    assert len(got) == 1 and got[0]["availability"] == Availability.CONFIRMED.value


def test_fundamental_missing_public_ts_rejected_not_backfilled():
    p = DataPortal()
    p.register_fundamental({"indicator_id": "cu.tc_rc", "industry": "铜", "period": "2025-12",
                            "value": 55.0, "source": "x", "first_public_ts": "",
                            "effective_ts": "2025-12-31", "fetched_ts": "2026-01-05",
                            "revision": 0, "completeness": "COMPLETE"})
    r = p.pit_records("cu.tc_rc", "2026-06-30")
    assert r["records"] == [] and r["status"] in (Availability.DATA_BLOCKED.value, Availability.UNKNOWN.value)
    assert any("被拒" in a for a in p._annos)


def test_catalog_starts_blocked():
    assert INDICATOR_CATALOG["pig.sow_stock"]["status"] == Availability.DATA_BLOCKED.value
    assert INDICATOR_CATALOG["growth.demand"]["status"] == Availability.DATA_BLOCKED.value
    p = DataPortal(data_mode=C.PIT_STRICT)
    r = p.pit_records("au.real_rate", "2026-06-30")
    assert r["status"] == Availability.DATA_BLOCKED.value and "strict_effect" in r


# ── 行业信任分层(§7.2 不静默) ──────────────────────────────
def test_no_sw_pit_verified_ever(portal):
    mp = IndustryMembershipProvider()
    for code in ("000998", "600438", "300970"):
        s = mp.sector_of(code, "2026-06-30")
        assert s["sw_trust"] in (MembershipTrust.SW_PARTIAL.value, MembershipTrust.UNKNOWN.value)
    b = IndustrySeriesBuilder(portal)
    pack = b.index_for("养殖业", "2026-06-30")
    if pack["series"] is None:                    # 名称对齐池快照(跳过空sector)
        ind = next(i for i in sorted(set(portal.price_panel()["sector"])) if i)
        pack = b.index_for(ind, "2026-06-30")
    assert pack["status"] == Availability.PROXY_ONLY.value
    assert pack["trust"] == MembershipTrust.CURRENT_SNAPSHOT_ONLY.value


# ── PIT 无泄漏特征(§9.1) ──────────────────────────────────
def test_features_no_future_leak(portal):
    b = IndustrySeriesBuilder(portal)
    inds = sorted(set(portal.price_panel()["sector"]))
    ind = [i for i in inds if i][0]
    day = "2024-06-28"
    f1 = stage_features(b, ind, day)
    idx, mkt = b._all()
    b2 = IndustrySeriesBuilder(portal)
    b2._idx = idx.loc[:day]
    b2._mkt = mkt.loc[:day]
    f2 = stage_features(b2, ind, day)
    assert f1 == f2, "前缀截断后特征必须恒等(存在未来泄漏即失败)"


def test_classify_state_contract():
    base = {"status": "PROXY_ONLY", "trend_250d": -0.2, "range_pos_250d": 0.35, "above_ma250": False,
            "trend_60d": -0.05, "mom_12m": -0.1, "no_new_low_60d": False, "base_rising_120d": False,
            "below_long_trend": True, "long_trend_slope_down": True}
    assert classify_state(dict(base)) == LongCycleState.DOWNTREND.value
    assert classify_state(dict(base, range_pos_250d=0.1)) == LongCycleState.BOTTOM_WATCH.value
    assert classify_state(dict(base, range_pos_250d=None)) == LongCycleState.UNKNOWN.value
    assert classify_state(dict(base, range_pos_250d=0.6, below_long_trend=True,
                               long_trend_slope_down=True, trend_250d=-0.2)) == LongCycleState.TREND_IMPAIRED.value
    assert classify_state(dict(base, above_ma250=True, trend_250d=0.2, mom_12m=0.1, trend_60d=0.05,
                               range_pos_250d=0.8)) == LongCycleState.EXPANSION.value
    assert classify_state(dict(base, no_new_low_60d=True, base_rising_120d=True,
                               range_pos_250d=0.2)) == LongCycleState.BOTTOM_FORMING.value


# ── 历史池分层边界 ─────────────────────────────────────────
def test_universe_buckets(portal):
    up = U.UniverseProvider(portal)
    ub = up.buckets_at("2026-06-30")
    assert ub["market_wide"]["status"] == Availability.DATA_BLOCKED.value
    assert ub["market_wide"]["delisted_to_asof_n"] >= 1
    assert "000961" in {c for c in U.DELISTED_MARKET_WIDE if U.DELISTED_MARKET_WIDE[c]["out"] <= "2026-06-30"}
    assert all(r["out"] > "2026-06-30" for r in U.FUTURE_OUTDATE.values()) or not U.FUTURE_OUTDATE
    assert "SURVIVORSHIP_BIAS_PARTIAL" in ub["caveats"]["survivorship"]
    assert ub["data_layers"]["HAS_QUOTES_T"], "面板内该日应有行情股非空"


def test_pool_at_shared_ref_isolated(portal):
    up = U.UniverseProvider(portal)
    ub = up.buckets_at("2026-06-30")
    ub["pool_current"]["TRADEABLE"].append("ZZZZZZ")
    import ashare_tradeable_pool as tp
    again = tp.pool_at("2026-06-30")["buckets"]["TRADEABLE"]
    assert "ZZZZZZ" not in again, "pool_at返回共享引用, universe必须deepcopy隔离"


# ── 阶段B产物存在且带溯源 ──────────────────────────────────
@pytest.mark.parametrize("f", ["data_quality_report.json", "universe_buckets_sample.json",
                                "industry_stage_snapshot.json"])
def test_phaseB_artifacts(f):
    p = os.path.join(C.OUTPUT_DIR, f)
    assert os.path.exists(p), "先运行 tools/phaseB_report.py"
    d = json.load(open(p, encoding="utf-8"))
    assert "_provenance" in d and d["_provenance"]["config_hash"] == C.config_hash()
