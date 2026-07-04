"""KEPCOAdapter 유닛 테스트 (실행계획 §2.6).

2026-07-03 확정된 "전자입찰 계약정보" API 명세 기반.
실제 호출 없이 fixture(tests/fixtures/kepco/notices.json)로
파싱 / normalize 매핑 / 코드값 변환 / 100억 필터 / 장기계속 / 90일 분할을 검증.
"""
import json
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.adapters.kepco import KEPCOAdapter, COMPANY_IDS
from src.adapters.base import CONSTRUCTION_MIN_PRICE

FIXTURE = Path(__file__).parent / "fixtures" / "kepco" / "notices.json"


@pytest.fixture
def adapter():
    return KEPCOAdapter(service_key="test-key-40-chars")


@pytest.fixture
def rows(adapter):
    return adapter._parse_response(FIXTURE.read_text(encoding="utf-8"))


def _mock_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


# ------------------------------------------------------------------ #
# 생성자 / 인증                                                        #
# ------------------------------------------------------------------ #

def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("KEPCO_API_KEY", raising=False)
    with pytest.raises(ValueError):
        KEPCOAdapter()


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("KEPCO_API_KEY", "env-key")
    assert KEPCOAdapter().service_key == "env-key"


def test_company_ids_for_phase2_reuse():
    """발전 자회사 companyId 표 — Phase 2 Wave B 재사용 전제."""
    assert COMPANY_IDS["KEPCO"] == "COM01"
    assert COMPANY_IDS["EWP"] == "COM08"
    a = KEPCOAdapter(service_key="k", company_id=COMPANY_IDS["KOMIPO"])
    assert a.company_id == "COM05"


# ------------------------------------------------------------------ #
# 응답 파싱                                                            #
# ------------------------------------------------------------------ #

def test_parse_fixture(rows):
    assert len(rows) == 4
    assert rows[0]["no"] == "R2026070100001"


def test_parse_single_dict_wrapped(adapter):
    assert adapter._parse_response('{"data": {"no": "K1"}}') == [{"no": "K1"}]


def test_parse_empty_data(adapter):
    assert adapter._parse_response('{"data": []}') == []


def test_parse_missing_data_raises(adapter):
    with pytest.raises(RuntimeError, match="오류 응답"):
        adapter._parse_response('{"errMsg": "INVALID API KEY"}')


def test_parse_non_json_raises(adapter):
    with pytest.raises(RuntimeError, match="비JSON"):
        adapter._parse_response("<html>Service Unavailable</html>")


# ------------------------------------------------------------------ #
# fetch_notices — 요청 파라미터 / 90일 분할                            #
# ------------------------------------------------------------------ #

def test_fetch_notices_params(adapter):
    with patch("src.adapters.kepco.get_with_retry",
               return_value=_mock_response(FIXTURE.read_text(encoding="utf-8"))) as mock_get:
        rows = list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))
    assert len(rows) == 4
    assert mock_get.call_count == 1
    params = mock_get.call_args.args[1]
    assert params["noticeBeginDate"] == "20260601"
    assert params["noticeEndDate"] == "20260630"
    assert params["apiKey"] == "test-key-40-chars"
    assert params["companyId"] == "COM01"
    assert params["returnType"] == "json"


def test_fetch_notices_splits_over_90_days(adapter):
    """91일 이상 범위는 90일 단위로 분할 호출해야 함."""
    with patch("src.adapters.kepco.get_with_retry",
               return_value=_mock_response('{"data": []}')) as mock_get:
        list(adapter.fetch_notices(date(2026, 1, 1), date(2026, 6, 30)))
    # 1/1~6/30 = 181일 → 90 + 90 + 1 = 3회
    assert mock_get.call_count == 3
    p1 = mock_get.call_args_list[0].args[1]
    p2 = mock_get.call_args_list[1].args[1]
    assert p1["noticeBeginDate"] == "20260101"
    assert p1["noticeEndDate"] == "20260331"    # 90일째
    assert p2["noticeBeginDate"] == "20260401"


def test_fetch_notices_90_days_single_call(adapter):
    with patch("src.adapters.kepco.get_with_retry",
               return_value=_mock_response('{"data": []}')) as mock_get:
        list(adapter.fetch_notices(date(2026, 1, 1), date(2026, 3, 31)))
    assert mock_get.call_count == 1


# ------------------------------------------------------------------ #
# normalize — 필드 매핑                                                #
# ------------------------------------------------------------------ #

def test_normalize_construction_notice(adapter, rows):
    n = adapter.normalize(rows[0])
    assert n["source"] == "kepco"
    assert n["notice_id"] == "kepco:R2026070100001:1"
    assert n["notice_no"] == "R2026070100001"
    assert n["agency_code"] == "KEPCO"
    assert n["title"].startswith("345kV")
    assert n["work_type"] == "공사"                    # itemType=Construction
    assert n["construction_type"] == "종합"            # etc: 종합건설업
    assert n["is_long_term_continuing"] is True        # 건명에 "장기계속"
    assert n["bid_method"] == "제한경쟁"               # competitionType=Limited
    assert n["estimated_price"] == 25_000_000_000      # presumedPrice
    assert n["vat_included"] is False                  # 추정가격은 VAT 제외 정의
    assert n["posted_at"] == "2026-06-01T09:00:00"     # noticeDate 14자리
    assert n["bid_open_at"] == "2026-06-15T14:00:00"   # endDatetime
    assert n["status"] == "공고진행"                   # PreAttendProgress
    assert n["_place_name"] == "한국전력공사 경기북부건설본부"
    assert n["_bid_type"] == "TotalEvalSuccess"
    assert n["source_hash"]


def test_normalize_product_notice(adapter, rows):
    """자재구매(Product): itemType='-' → purchaseType으로 물품 판별."""
    n = adapter.normalize(rows[2])
    assert n["work_type"] == "물품"
    assert n["estimated_price"] is None                # presumedPrice="-"
    assert n["bid_open_at"] is None                    # endDatetime="-"
    assert n["posted_at"] == "2026-06-03"              # noticeDate 8자리
    assert n["status"] == "입찰진행"                   # AttendProgress
    assert n["bid_method"] is None                     # competitionType="-"


def test_normalize_service_notice(adapter, rows):
    """itemType=Service → 용역 (100억 넘어도 공사 필터에서 제외돼야 함)."""
    n = adapter.normalize(rows[3])
    assert n["work_type"] == "용역"
    assert n["bid_method"] == "수의"                   # Private
    assert n["status"] == "공고종료"                   # Final
    assert adapter.passes_filter(n) is False


def test_normalize_dash_placeholder_cleaned(adapter):
    n = adapter.normalize({"no": "K1", "name": "-", "presumedPrice": "-",
                           "itemType": "-", "purchaseType": "-"})
    assert n["title"] is None
    assert n["estimated_price"] is None
    assert n["work_type"] == "미분류"


def test_normalize_source_hash_stable(adapter):
    raw = {"no": "K1", "name": "공사"}
    assert adapter.normalize(raw)["source_hash"] == adapter.normalize(dict(raw))["source_hash"]


def test_work_type_construction_service_falls_back_to_title(adapter):
    """purchaseType=ConstructionService인데 itemType이 없으면 건명으로 판별."""
    n = adapter.normalize({"no": "K1", "purchaseType": "ConstructionService",
                           "name": "○○ 건설공사"})
    assert n["work_type"] == "공사"
    n2 = adapter.normalize({"no": "K2", "purchaseType": "ConstructionService",
                            "name": "○○ 감리"})
    assert n2["work_type"] == "용역"


# ------------------------------------------------------------------ #
# 100억 필터 / unpriced                                                #
# ------------------------------------------------------------------ #

def test_passes_filter_fixture_rows(adapter, rows):
    normalized = [adapter.normalize(r) for r in rows]
    passed = [n for n in normalized if adapter.passes_filter(n)]
    assert [n["notice_no"] for n in passed] == ["R2026070100001"]  # 공사 250억만
    # 90억 공사는 미달, 물품·용역은 제외
    below = adapter.normalize(rows[1])
    assert below["estimated_price"] == 9_000_000_000
    assert adapter.passes_filter(below) is False


def test_passes_filter_threshold(adapter):
    at = {"work_type": "공사", "estimated_price": CONSTRUCTION_MIN_PRICE}
    under = {"work_type": "공사", "estimated_price": CONSTRUCTION_MIN_PRICE - 1}
    assert adapter.passes_filter(at) is True
    assert adapter.passes_filter(under) is False


def test_is_unpriced_construction(adapter):
    n = adapter.normalize({"no": "K1", "itemType": "Construction",
                           "name": "가격 미공개 공사", "presumedPrice": "-"})
    assert adapter.is_unpriced(n) is True


# ------------------------------------------------------------------ #
# 장기계속 판별                                                        #
# ------------------------------------------------------------------ #

def test_long_term_from_bid_type_detail(adapter):
    n = adapter.normalize({"no": "K1", "name": "송전공사",
                           "bidTypeDetail": "장기계속계약 2차"})
    assert n["is_long_term_continuing"] is True


def test_not_long_term(adapter, rows):
    assert adapter.normalize(rows[1])["is_long_term_continuing"] is False


# ------------------------------------------------------------------ #
# 낙찰·계약 — 본 API 미제공 (G2B 보완)                                 #
# ------------------------------------------------------------------ #

def test_awards_contracts_empty(adapter):
    with patch("src.adapters.kepco.get_with_retry") as mock_get:
        assert list(adapter.fetch_awards(date(2026, 6, 1), date(2026, 6, 30))) == []
        assert list(adapter.fetch_contracts(date(2026, 6, 1), date(2026, 6, 30))) == []
    mock_get.assert_not_called()


# ------------------------------------------------------------------ #
# health_check                                                         #
# ------------------------------------------------------------------ #

def test_health_check_ok(adapter):
    with patch("src.adapters.kepco.get_with_retry",
               return_value=_mock_response('{"data": []}')):
        assert adapter.health_check() is True


def test_health_check_fail_on_error_payload(adapter):
    with patch("src.adapters.kepco.get_with_retry",
               return_value=_mock_response('{"errMsg": "INVALID KEY"}')):
        assert adapter.health_check() is False


def test_health_check_fail_on_connection(adapter):
    with patch("src.adapters.kepco.get_with_retry", side_effect=ConnectionError):
        assert adapter.health_check() is False
