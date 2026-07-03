from datetime import date

import pandas as pd

from src.run_monthly import join_all, month_bounds


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
