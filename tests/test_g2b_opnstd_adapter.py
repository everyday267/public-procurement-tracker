"""G2BOpnStdAdapter (PubDataOpnStdService) 유닛 테스트.

실제 API 호출 없이 normalize / 필터 / VAT 환산 / 낙찰·계약 필드를 검증.
"""
import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from src.adapters.g2b_opnstd import G2BOpnStdAdapter
from src.adapters.base import CONSTRUCTION_MIN_PRICE


@pytest.fixture
def adapter():
    return G2BOpnStdAdapter(api_key="test-key")


def test_encoding_key_is_normalized():
    """이미 URL 인코딩된 키(%2B, %2F)를 unquote 하여 이중 인코딩을 방지해야 함."""
    enc = "abc%2Bdef%2Fghi%3D%3D"
    a = G2BOpnStdAdapter(api_key=enc)
    assert a.api_key == "abc+def/ghi=="


def test_decoding_key_unchanged():
    """이미 디코딩된 원문 키는 unquote 해도 그대로여야 함 (멱등)."""
    dec = "abc+def/ghi=="
    a = G2BOpnStdAdapter(api_key=dec)
    assert a.api_key == "abc+def/ghi=="


# ------------------------------------------------------------------ #
# normalize 테스트 — 입찰공고 raw                                      #
# ------------------------------------------------------------------ #

def test_normalize_basic(adapter):
    """source, notice_id 필드가 g2b_opnstd 접두사를 써야 함."""
    raw = {
        "bidNtceNo": "R25BK00933743",
        "bidNtceOrd": "000",
        "bidNtceNm": "종합공사 장기계속 테스트",
        "bsnsDivNm": "공사",
        "bidprcPsblIndstrytyNm": "종합공사업",
        "cntrctCnclsMthdNm": "장기계속",
        "presmptPrce": "12000000000",
        "bidNtceSttusNm": "공고중",
        "bidNtceDate": "2025-07-01",
        "opengDate": "2025-07-08",
    }
    n = adapter.normalize(raw)
    assert n["source"] == "g2b_opnstd"
    assert n["notice_id"] == "g2b_opnstd:R25BK00933743:0"
    assert n["construction_type"] == "종합"
    assert n["is_long_term_continuing"] is True
    assert n["estimated_price"] == 12_000_000_000
    assert n["posted_at"] == "2025-07-01"
    assert n["bid_open_at"] == "2025-07-08"


def test_normalize_specialist(adapter):
    raw = {
        "bidNtceNo": "R25BK00999999",
        "bidNtceOrd": "0",
        "bidNtceNm": "전문공사 도장",
        "bsnsDivNm": "공사",
        "bidprcPsblIndstrytyNm": "전문공사업",
        "presmptPrce": "15000000000",
    }
    n = adapter.normalize(raw)
    assert n["construction_type"] == "전문"
    assert n["is_long_term_continuing"] is False


# ------------------------------------------------------------------ #
# normalize 테스트 — 낙찰정보 raw                                      #
# ------------------------------------------------------------------ #

def test_normalize_award_fields(adapter):
    """낙찰 raw에서 _award_* 필드가 정상 매핑되어야 함."""
    raw = {
        "bidNtceNo": "R25BK00925778",
        "bidNtceOrd": "000",
        "bsnsDivNm": "공사",
        "fnlSucsfCorpNm": "테스트건설()",
        "fnlSucsfCorpBizrno": "308-81-03521",
        "fnlSucsfAmt": "122845000",
        "fnlSucsfRt": "90.394",
        "presmptPrce": "13000000000",
    }
    n = adapter.normalize(raw)
    assert n["_award_corp"] == "테스트건설()"
    assert n["_award_corp_bizrno"] == "308-81-03521"
    assert n["_award_amt"] == 122_845_000
    assert n["_award_rate"] == "90.394"


# ------------------------------------------------------------------ #
# normalize 테스트 — 계약정보 raw                                      #
# ------------------------------------------------------------------ #

def test_normalize_contract_fields(adapter):
    """계약 raw에서 _contract_* 필드가 정상 매핑되어야 함."""
    raw = {
        "bidNtceNo": "R25BK00111111",
        "bidNtceOrd": "000",
        "bsnsDivNm": "공사",
        "cntrctAmt": "9800000000",
        "cntrctCnclsDate": "2025-08-01",
        "dmndInsttNm": "서울특별시 건설재난구",
        "presmptPrce": "10000000000",
    }
    n = adapter.normalize(raw)
    assert n["_contract_amt"] == 9_800_000_000
    assert n["_contract_date"] == "2025-08-01"
    assert n["_demand_inst"] == "서울특별시 건설재난구"


# ------------------------------------------------------------------ #
# passes_filter / is_unpriced 테스트                                  #
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
        "bidNtceNo": "R25BK00000001",
        "bidNtceOrd": "0",
        "bidNtceNm": "종합공사 VAT 포함",
        "bsnsDivNm": "공사",
        "presmptPrce": "11000000000",
        "vatIncldYn": "Y",
    }
    n = adapter.normalize(raw)
    assert n["vat_included"] is True
    assert n["estimated_price"] == int(11_000_000_000 / 1.1)


# ------------------------------------------------------------------ #
# 날짜 분할 테스트 — 7일 단위 쫙크 분할                             #
# ------------------------------------------------------------------ #

def test_weekly_chunk_split(adapter):
    """쫙크 분할 로직: 14일 범위 → _request 2회 호출 확인."""
    with patch.object(adapter, "_request", return_value=[]) as mock_req:
        list(adapter.fetch_contracts(
            since=date(2026, 6, 1),
            until=date(2026, 6, 14),
        ))
        assert mock_req.call_count == 2


def test_weekly_chunk_exact_7days(adapter):
    """7일 정확히 → _request 1회만 호출."""
    with patch.object(adapter, "_request", return_value=[]) as mock_req:
        list(adapter.fetch_contracts(
            since=date(2026, 6, 1),
            until=date(2026, 6, 7),
        ))
        assert mock_req.call_count == 1


def test_award_weekly_chunk_split(adapter):
    """낙찰 쫙크 분할: 21일 범위 → _request 3회."""
    with patch.object(adapter, "_request", return_value=[]) as mock_req:
        list(adapter.fetch_awards(
            since=date(2026, 6, 1),
            until=date(2026, 6, 21),
        ))
        assert mock_req.call_count == 3
