import os
import sqlite3
import tempfile
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

import src.run_monthly as rm
from src.run_monthly import join_all, month_bounds
from src.adapters.g2b_opnstd import G2BOpnStdAdapter
from src.adapters.kr_rail import KRRailAdapter
from src.adapters.kepco import KEPCOAdapter
from src.adapters.lh import LHAdapter


def test_month_bounds_31day():
    start, end = month_bounds("2026-05")
    assert start == date(2026, 5, 1)
    assert end == date(2026, 5, 31)


def test_month_bounds_leap_feb():
    start, end = month_bounds("2028-02")
    assert start == date(2028, 2, 1)
    assert end == date(2028, 2, 29)


def test_join_all_lh_style():
    notices = [{"notice_no": "N1", "title": "공사1", "estimated_price": 12_000_000_000}]
    awards = [{"notice_no": "N1", "bidder_name": "A건설", "winner_status": "낙찰", "award_price": 11_000_000_000}]
    contracts = [{"notice_no": "N1", "contract_price": 11_000_000_000, "contractor_name": "A건설"}]

    df = join_all("lh", notices, awards, contracts)
    assert len(df) == 1
    assert df.iloc[0]["source"] == "lh"
    assert df.iloc[0]["winner_name"] == "A건설"
    assert df.iloc[0]["contract_price"] == 11_000_000_000


def test_join_all_g2b_style_missing_columns_filled():
    """G2B계열은 zone_hq 등 LH 전용 컬럼이 없다 — NaN으로 채워져야 함."""
    notices = [{"notice_no": "N2", "title": "공사2", "estimated_price": 15_000_000_000}]
    awards = [{"notice_no": "N2", "bidder_name": "B건설", "award_price": 14_000_000_000}]
    contracts = []

    df = join_all("g2b_opnstd", notices, awards, contracts)
    assert len(df) == 1
    assert pd.isna(df.iloc[0]["zone_hq"])
    assert df.iloc[0]["winner_name"] == "B건설"
    assert pd.isna(df.iloc[0]["contract_price"])


def test_join_all_empty_inputs():
    df = join_all("lh", [], [], [])
    assert len(df) == 0
    assert "source" in df.columns


def test_g2b_family_fetches_once_and_kr_rail_is_subset():
    """g2b_opnstd·kr_rail 동시 활성 시 전국 fetch는 1회만, kr_rail은 국가철도공단분만."""
    n_kr = {"bidNtceNo": "K1", "bidNtceOrd": "0", "bidNtceNm": "철도공사",
            "bsnsDivNm": "공사", "presmptPrce": "30000000000", "dmndInsttNm": "국가철도공단"}
    n_g = {"bidNtceNo": "G1", "bidNtceOrd": "0", "bidNtceNm": "일반공사",
           "bsnsDivNm": "공사", "presmptPrce": "20000000000", "dmndInsttNm": "서울시"}
    a_kr = {"bidNtceNo": "K1", "bidNtceOrd": "0", "bsnsDivNm": "공사",
            "fnlSucsfCorpNm": "철도건설", "fnlSucsfAmt": "29000000000",
            "presmptPrce": "30000000000", "dmndInsttNm": "국가철도공단"}
    c_kr = {"bidNtceNo": "K1", "bidNtceOrd": "0", "bsnsDivNm": "공사",
            "cntrctAmt": "29000000000", "cntrctCnclsDate": "2026-06-15",
            "presmptPrce": "30000000000", "dmndInsttNm": "국가철도공단"}

    calls = {"n": 0, "a": 0, "c": 0}

    def notices(s, u):
        calls["n"] += 1
        return iter([n_kr, n_g])

    def awards_scoped(notice_nos, s, u):
        calls["a"] += 1
        return iter([a_kr])

    def contracts_scoped(notice_nos, s, u):
        calls["c"] += 1
        return iter([c_kr])

    with tempfile.TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"G2B_API_KEY": "dummy"}), \
             patch.object(G2BOpnStdAdapter, "fetch_notices", side_effect=notices), \
             patch.object(G2BOpnStdAdapter, "fetch_awards_scoped", side_effect=awards_scoped), \
             patch.object(G2BOpnStdAdapter, "fetch_contracts_scoped", side_effect=contracts_scoped), \
             patch.object(KRRailAdapter, "_is_kr_rail",
                          side_effect=lambda r: "국가철도공단" in (r.get("dmndInsttNm", "") or "")):
            rm.run("2026-06", db_path=f"{tmp}/t.db", output_dir=f"{tmp}/o",
                   sources=["g2b_opnstd", "kr_rail"])

        # 공고 전국 fetch 1회 + 계약·낙찰 스코프 조회 각 1회 (소스가 2개여도 공유).
        assert calls == {"n": 1, "a": 1, "c": 1}

        conn = sqlite3.connect(f"{tmp}/t.db")
        g2b_nos = {r[0] for r in conn.execute(
            "SELECT notice_no FROM notices WHERE source='g2b_opnstd'").fetchall()}
        kr_nos = {r[0] for r in conn.execute(
            "SELECT notice_no FROM notices WHERE source='kr_rail'").fetchall()}
        conn.close()
        assert g2b_nos == {"G1", "K1"}   # 전국 전체
        assert kr_nos == {"K1"}          # 국가철도공단분만


# ------------------------------------------------------------------ #
# 멀티 어댑터 오케스트레이션 (실행계획 §2.3/§2.6)                       #
# ------------------------------------------------------------------ #

KEPCO_RAW = {"purchaseType": "ConstructionService", "itemType": "Construction",
             "no": "R2026-K1", "name": "345kV 송전선로 건설공사(장기계속)",
             "presumedPrice": "25000000000", "noticeDate": "20260601090000",
             "endDatetime": "20260615140000", "progressState": "PreAttendProgress",
             "competitionType": "Limited", "placeName": "한국전력공사"}


def test_kepco_source_registered():
    assert "kepco" in rm.SOURCES
    assert "kepco" in rm.SELF_SCOPED


def test_kepco_skipped_without_api_key(tmp_path):
    """KEPCO_API_KEY 미설정 시 경고 후 skip — 다른 소스 수집은 계속돼야 함."""
    env = {"LH_API_KEY": "dummy"}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("KEPCO_API_KEY", None)
        with patch.object(LHAdapter, "fetch_notices", return_value=iter([])), \
             patch.object(LHAdapter, "fetch_awards", return_value=iter([])), \
             patch.object(LHAdapter, "fetch_contracts", return_value=iter([])):
            rm.run("2026-06", db_path=str(tmp_path / "t.db"),
                   output_dir=str(tmp_path / "o"), sources=["lh", "kepco"])

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    runs = dict(conn.execute("SELECT source, status FROM source_runs").fetchall())
    conn.close()
    assert runs.get("lh") == "success"
    assert "kepco" not in runs          # skip: 에러가 아니라 미실행


def test_kepco_success_run_records_and_filters(tmp_path):
    """kepco 수집 성공 시 100억↑ 공사 공고 적재 + source_runs=success."""
    with patch.dict(os.environ, {"KEPCO_API_KEY": "dummy"}), \
         patch.object(KEPCOAdapter, "fetch_notices", return_value=iter([KEPCO_RAW])):
        rm.run("2026-06", db_path=str(tmp_path / "t.db"),
               output_dir=str(tmp_path / "o"), sources=["kepco"])

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    runs = dict(conn.execute("SELECT source, status FROM source_runs").fetchall())
    notices = conn.execute(
        "SELECT notice_id, estimated_price FROM notices WHERE source='kepco'").fetchall()
    conn.close()
    assert runs == {"kepco": "success"}
    assert notices == [("kepco:R2026-K1:1", 25_000_000_000)]
    assert (tmp_path / "o" / "kepco_joined_202606.csv").exists()


def test_source_failure_is_isolated(tmp_path):
    """한 소스(lh) fetch 실패가 다른 소스(kepco) 수집을 막지 않아야 함 (§2.3)."""
    with patch.dict(os.environ, {"KEPCO_API_KEY": "dummy", "LH_API_KEY": "dummy"}), \
         patch.object(LHAdapter, "fetch_notices",
                      side_effect=ConnectionError("LH down")), \
         patch.object(KEPCOAdapter, "fetch_notices", return_value=iter([KEPCO_RAW])):
        rm.run("2026-06", db_path=str(tmp_path / "t.db"),
               output_dir=str(tmp_path / "o"), sources=["lh", "kepco"])

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    rows = {s: (st, em) for s, st, em in conn.execute(
        "SELECT source, status, error_message FROM source_runs").fetchall()}
    conn.close()
    assert rows["kepco"][0] == "success"
    assert rows["lh"][0] == "error"
    assert "LH down" in rows["lh"][1]


def test_all_sources_fail_raises(tmp_path):
    """모든 소스 실패 시 RuntimeError로 배치 실패를 알려야 함."""
    with patch.dict(os.environ, {"KEPCO_API_KEY": "dummy"}), \
         patch.object(KEPCOAdapter, "fetch_notices",
                      side_effect=ConnectionError("down")):
        with pytest.raises(RuntimeError, match="모든 소스"):
            rm.run("2026-06", db_path=str(tmp_path / "t.db"),
                   output_dir=str(tmp_path / "o"), sources=["kepco"])


def test_unknown_source_rejected(tmp_path):
    with pytest.raises(ValueError, match="알 수 없는 source"):
        rm.run("2026-06", db_path=str(tmp_path / "t.db"),
               output_dir=str(tmp_path / "o"), sources=["khnp"])


def test_kepco_unpriced_isolated(tmp_path):
    """추정가격 미공개 공사는 notices가 아닌 notices_unpriced로 격리 (PRD §3)."""
    unpriced = dict(KEPCO_RAW, no="R2026-K2", name="가격미공개 건설공사",
                    presumedPrice="-")
    with patch.dict(os.environ, {"KEPCO_API_KEY": "dummy"}), \
         patch.object(KEPCOAdapter, "fetch_notices",
                      return_value=iter([KEPCO_RAW, unpriced])):
        rm.run("2026-06", db_path=str(tmp_path / "t.db"),
               output_dir=str(tmp_path / "o"), sources=["kepco"])

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    priced = [r[0] for r in conn.execute(
        "SELECT notice_no FROM notices WHERE source='kepco'").fetchall()]
    unpriced_rows = [r[0] for r in conn.execute(
        "SELECT notice_no FROM notices_unpriced WHERE source='kepco'").fetchall()]
    conn.close()
    assert priced == ["R2026-K1"]
    assert unpriced_rows == ["R2026-K2"]


def test_is_target_contract_installment_uses_total():
    """차수 계약(차수 금액<100억, 총액≥100억)은 총액 기준으로 대상."""
    from src.run_monthly import _is_target_contract
    assert _is_target_contract({"contract_price": 3_000_000_000,
                                "total_contract_price": 38_000_000_000,
                                "bsns_div": "공사"}) is True
    assert _is_target_contract({"contract_price": 3_000_000_000,
                                "total_contract_price": 8_000_000_000,
                                "bsns_div": "공사"}) is False
    assert _is_target_contract({"contract_price": 12_000_000_000,
                                "total_contract_price": None,
                                "bsns_div": None}) is True
    assert _is_target_contract({"contract_price": None,
                                "total_contract_price": None}) is False
