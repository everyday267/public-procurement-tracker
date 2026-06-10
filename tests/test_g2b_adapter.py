import pytest
from unittest.mock import patch, MagicMock
from datetime import date

from src.adapters.g2b import G2BAdapter
from src.adapters.base import CONSTRUCTION_MIN_PRICE


@pytest.fixture
def adapter():
    return G2BAdapter(api_key="test-key")


# ------------------------------------------------------------------ #
# normalize 테스트                                                     #
# ------------------------------------------------------------------ #

def test_normalize_basic(adapter):
    raw = {
        "bidNtceNo": "20260001",
        "bidNtceOrd": "1",
        "bidNtceNm": "종합 공사 장기계속 테스트",
        "bsnsDivNm": "공사",
        "indstrytyNm": "종합",
        "cntrctCnclsMthdNm": "장기계속",
        "asignBdgtAmt": "12000000000",
        "bidNtceSttusNm": "공고중",
    }
    n = adapter.normalize(raw)
    assert n["notice_id"] == "g2b:20260001:1"
    assert n["construction_type"] == "종합"
    assert n["is_long_term_continuing"] is True
    assert n["estimated_price"] == 12_000_000_000


def test_normalize_specialist(adapter):
    raw = {
        "bidNtceNo": "20260002",
        "bidNtceOrd": "0",
        "bidNtceNm": "전문공사 도장",
        "bsnsDivNm": "공사",
        "indstrytyNm": "전문",
        "asignBdgtAmt": "15000000000",
    }
    n = adapter.normalize(raw)
    assert n["construction_type"] == "전문"
    assert n["is_long_term_continuing"] is False


# ------------------------------------------------------------------ #
# passes_filter 테스트                                                 #
# ------------------------------------------------------------------ #

def test_passes_filter_above_threshold(adapter):
    n = {"work_type": "공사", "estimated_price": CONSTRUCTION_MIN_PRICE}
    assert adapter.passes_filter(n) is True


def test_passes_filter_below_threshold(adapter):
    n = {"work_type": "공사", "estimated_price": 5_000_000_000}
    assert adapter.passes_filter(n) is False


def test_passes_filter_non_construction(adapter):
    n = {"work_type": "용역", "estimated_price": 20_000_000_000}
    assert adapter.passes_filter(n) is False


def test_is_unpriced(adapter):
    n = {"work_type": "공사", "estimated_price": None}
    assert adapter.is_unpriced(n) is True


# ------------------------------------------------------------------ #
# VAT 환산 테스트                                                      #
# ------------------------------------------------------------------ #

def test_vat_exclusion(adapter):
    raw = {
        "bidNtceNo": "20260003",
        "bidNtceOrd": "0",
        "bidNtceNm": "종합공사",
        "bsnsDivNm": "공사",
        "asignBdgtAmt": "11000000000",
        "vatIncldYn": "Y",
    }
    n = adapter.normalize(raw)
    assert n["vat_included"] is True
    assert n["estimated_price"] == int(11_000_000_000 / 1.1)
