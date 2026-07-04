"""KRRailAdapter 유닛 테스트 (실행계획 §2.6, G-4 보강).

_is_kr_rail 필터 / source·notice_id 덮어쓰기 / fetch 2차 필터링 검증.
"""
import pytest
from datetime import date
from unittest.mock import patch

from src.adapters.kr_rail import KRRailAdapter, KR_INST_CODE
from src.adapters.g2b_opnstd import G2BOpnStdAdapter


@pytest.fixture
def adapter():
    return KRRailAdapter(api_key="test-key")


KR_ROW = {"bidNtceNo": "K1", "bidNtceOrd": "0", "bidNtceNm": "철도 노반신설 기타공사",
          "bsnsDivNm": "공사", "presmptPrce": "30000000000", "dmndInsttNm": "국가철도공단"}
OTHER_ROW = {"bidNtceNo": "G1", "bidNtceOrd": "0", "bidNtceNm": "도로 확장공사",
             "bsnsDivNm": "공사", "presmptPrce": "20000000000", "dmndInsttNm": "서울특별시"}


# ------------------------------------------------------------------ #
# _is_kr_rail 필터                                                     #
# ------------------------------------------------------------------ #

def test_is_kr_rail_by_dmnd_instt_name(adapter):
    """실서비스 개방표준 필드명(dmndInsttNm) 기준 — 2026-07 검증에서 확정."""
    assert adapter._is_kr_rail(KR_ROW) is True
    assert adapter._is_kr_rail(OTHER_ROW) is False


def test_is_kr_rail_by_ntce_instt_name(adapter):
    assert adapter._is_kr_rail({"ntceInsttNm": "국가철도공단"}) is True


def test_is_kr_rail_legacy_field_names(adapter):
    """구 표기(instNm/dminsttNm)도 하위 호환으로 매칭돼야 함."""
    assert adapter._is_kr_rail({"instNm": "국가철도공단 수도권본부"}) is True
    assert adapter._is_kr_rail({"dminsttNm": "국가철도공단"}) is True


def test_is_kr_rail_by_inst_code(adapter):
    assert adapter._is_kr_rail({"dmndInsttCd": KR_INST_CODE}) is True
    assert adapter._is_kr_rail({"ntceInsttCd": KR_INST_CODE}) is True
    assert adapter._is_kr_rail({"instCd": KR_INST_CODE}) is True


def test_is_kr_rail_empty_row(adapter):
    assert adapter._is_kr_rail({}) is False


# ------------------------------------------------------------------ #
# fetch — 상위(G2B) 결과를 2차 필터링                                  #
# ------------------------------------------------------------------ #

def test_fetch_notices_filters_to_kr_rail(adapter):
    with patch.object(G2BOpnStdAdapter, "fetch_notices",
                      return_value=iter([KR_ROW, OTHER_ROW])):
        rows = list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))
    assert rows == [KR_ROW]


def test_fetch_awards_filters_to_kr_rail(adapter):
    with patch.object(G2BOpnStdAdapter, "fetch_awards",
                      return_value=iter([OTHER_ROW])):
        assert list(adapter.fetch_awards(date(2026, 6, 1), date(2026, 6, 30))) == []


def test_fetch_contracts_filters_to_kr_rail(adapter):
    with patch.object(G2BOpnStdAdapter, "fetch_contracts",
                      return_value=iter([KR_ROW, KR_ROW, OTHER_ROW])):
        rows = list(adapter.fetch_contracts(date(2026, 6, 1), date(2026, 6, 30)))
    assert len(rows) == 2


# ------------------------------------------------------------------ #
# normalize — source/agency/notice_id 덮어쓰기                         #
# ------------------------------------------------------------------ #

def test_normalize_overrides_source(adapter):
    n = adapter.normalize(KR_ROW)
    assert n["source"] == "kr_rail"
    assert n["agency_code"] == "KR_RAIL"
    assert n["notice_id"] == "kr_rail:K1:0"
    # G2B 정규화 로직은 그대로 재사용돼야 함
    assert n["work_type"] == "공사"
    assert n["estimated_price"] == 30_000_000_000


def test_normalize_keeps_g2b_award_passthrough_fields(adapter):
    raw = dict(KR_ROW, fnlSucsfCorpNm="철도건설(주)", fnlSucsfAmt="29000000000")
    n = adapter.normalize(raw)
    assert n["_award_corp"] == "철도건설(주)"
    assert n["_award_amt"] == 29_000_000_000


def test_class_attributes():
    assert KRRailAdapter.source == "kr_rail"
    assert KRRailAdapter.agency_codes == ["KR_RAIL"]
