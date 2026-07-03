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
