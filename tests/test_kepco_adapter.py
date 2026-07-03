"""KEPCOAdapter 유닛 테스트 (실행계획 §2.6).

실제 API 호출 없이 fixture(tests/fixtures/kepco/) 기반으로
normalize 필드 매핑 / VAT 환산 / 100억 필터 / 장기계속 판별 / 페이지네이션 검증.
"""
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.adapters.kepco import KEPCOAdapter
from src.adapters.base import CONSTRUCTION_MIN_PRICE

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kepco"


@pytest.fixture
def adapter():
    return KEPCOAdapter(service_key="test-key")


def _fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


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
    a = KEPCOAdapter()
    assert a.service_key == "env-key"


# ------------------------------------------------------------------ #
# XML/JSON 파싱                                                        #
# ------------------------------------------------------------------ #

def test_parse_xml_fixture(adapter):
    items, total = adapter._parse_response(_fixture("notices_page1.xml"))
    assert total == 3
    assert len(items) == 2
    assert items[0]["bidNo"] == "K2026-0601-001"
    assert items[0]["presmptPrc"] == "25000000000"


def test_parse_xml_error_code_raises(adapter):
    xml = """<response><header><resultCode>30</resultCode>
             <resultMsg>SERVICE KEY IS NOT REGISTERED</resultMsg></header></response>"""
    with pytest.raises(RuntimeError, match="resultCode=30"):
        adapter._parse_response(xml)


def test_parse_json_response(adapter):
    text = """{"response":{"header":{"resultCode":"00"},"body":{
        "items":{"item":[{"bidNo":"K1","bidNm":"테스트 공사"}]},"totalCount":1}}}"""
    items, total = adapter._parse_response(text)
    assert total == 1
    assert items[0]["bidNo"] == "K1"


def test_parse_json_single_item_dict(adapter):
    """items.item이 배열이 아닌 단일 dict여도 리스트로 감싸야 함."""
    text = """{"response":{"body":{"items":{"item":{"bidNo":"K1"}},"totalCount":1}}}"""
    items, total = adapter._parse_response(text)
    assert items == [{"bidNo": "K1"}]


# ------------------------------------------------------------------ #
# 페이지네이션 (fixture 2페이지)                                       #
# ------------------------------------------------------------------ #

def test_pagination_two_pages(adapter):
    pages = [_mock_response(_fixture("notices_page1.xml")),
             _mock_response(_fixture("notices_page2.xml"))]
    with patch("src.adapters.kepco.get_with_retry", side_effect=pages) as mock_get:
        rows = list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))
    assert len(rows) == 3
    assert mock_get.call_count == 2
    # 2번째 호출은 pageNo=2 여야 함
    assert mock_get.call_args_list[1].args[1]["pageNo"] == 2
    # 기간 파라미터가 YYYYMMDD로 전달돼야 함
    first_params = mock_get.call_args_list[0].args[1]
    assert first_params["startDate"] == "20260601"
    assert first_params["endDate"] == "20260630"
    assert first_params["serviceKey"] == "test-key"


def test_pagination_stops_when_total_reached(adapter):
    """1페이지에 totalCount만큼 다 오면 추가 호출 없음."""
    xml = _fixture("notices_page1.xml").replace(
        "<totalCount>3</totalCount>", "<totalCount>2</totalCount>")
    with patch("src.adapters.kepco.get_with_retry",
               return_value=_mock_response(xml)) as mock_get:
        rows = list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))
    assert len(rows) == 2
    assert mock_get.call_count == 1


# ------------------------------------------------------------------ #
# normalize — 필드 매핑                                                #
# ------------------------------------------------------------------ #

def test_normalize_basic(adapter):
    items, _ = adapter._parse_response(_fixture("notices_page1.xml"))
    n = adapter.normalize(items[0])
    assert n["source"] == "kepco"
    assert n["notice_id"] == "kepco:K2026-0601-001:1"
    assert n["notice_no"] == "K2026-0601-001"
    assert n["agency_code"] == "KEPCO"
    assert n["title"] == "345kV 송전선로 건설공사 (장기계속 1차)"
    assert n["work_type"] == "공사"
    assert n["estimated_price"] == 25_000_000_000
    assert n["vat_included"] is False
    assert n["posted_at"] == "2026-06-01"
    assert n["bid_open_at"] == "2026-06-15"
    assert n["status"] == "공고중"
    assert n["source_hash"]


def test_normalize_notice_id_uses_degree(adapter):
    n = adapter.normalize({"bidNo": "K1", "bidDegree": "3", "bidNm": "공사"})
    assert n["notice_id"] == "kepco:K1:3"


def test_normalize_field_candidates_fallback(adapter):
    """1순위 키가 없으면 후보 목록의 다음 키를 써야 함 (스키마 확정 전 방어)."""
    n = adapter.normalize({
        "bidNtceNo": "K9", "bidNtceNm": "○○ 전문공사",
        "bsnsDivNm": "공사", "asignBdgtAmt": "12000000000",
    })
    assert n["notice_no"] == "K9"
    assert n["title"] == "○○ 전문공사"
    assert n["work_type"] == "공사"
    assert n["estimated_price"] == 12_000_000_000
    assert n["construction_type"] == "전문"


def test_normalize_source_hash_stable(adapter):
    raw = {"bidNo": "K1", "bidNm": "공사"}
    assert adapter.normalize(raw)["source_hash"] == adapter.normalize(dict(raw))["source_hash"]


# ------------------------------------------------------------------ #
# VAT 환산                                                             #
# ------------------------------------------------------------------ #

def test_vat_included_converted(adapter):
    items, _ = adapter._parse_response(_fixture("notices_page1.xml"))
    n = adapter.normalize(items[1])  # vatYn=Y, 110억
    assert n["vat_included"] is True
    assert n["estimated_price"] == int(11_000_000_000 / 1.1)


def test_price_missing_is_none(adapter):
    n = adapter.normalize({"bidNo": "K1", "bidNm": "가격 미공개 공사"})
    assert n["estimated_price"] is None
    assert adapter.is_unpriced(n) is True


# ------------------------------------------------------------------ #
# 100억 필터                                                           #
# ------------------------------------------------------------------ #

def test_passes_filter_above_threshold(adapter):
    n = {"work_type": "공사", "estimated_price": CONSTRUCTION_MIN_PRICE}
    assert adapter.passes_filter(n) is True


def test_passes_filter_below_threshold(adapter):
    n = {"work_type": "공사", "estimated_price": 9_999_999_999}
    assert adapter.passes_filter(n) is False


def test_passes_filter_non_construction(adapter):
    items, _ = adapter._parse_response(_fixture("notices_page2.xml"))
    n = adapter.normalize(items[0])  # 물품
    assert n["work_type"] == "물품"
    assert adapter.passes_filter(n) is False


# ------------------------------------------------------------------ #
# 장기계속 판별                                                        #
# ------------------------------------------------------------------ #

def test_long_term_from_title(adapter):
    items, _ = adapter._parse_response(_fixture("notices_page1.xml"))
    assert adapter.normalize(items[0])["is_long_term_continuing"] is True   # 공고명에 장기계속
    assert adapter.normalize(items[1])["is_long_term_continuing"] is False  # 일반경쟁


def test_long_term_from_contract_method(adapter):
    n = adapter.normalize({"bidNo": "K1", "bidNm": "송전공사", "cntrctMthdNm": "장기계속"})
    assert n["is_long_term_continuing"] is True


# ------------------------------------------------------------------ #
# 낙찰·계약 — 오퍼레이션 미확정 시 빈 결과                              #
# ------------------------------------------------------------------ #

def test_awards_contracts_empty_until_op_confirmed(adapter):
    with patch("src.adapters.kepco.get_with_retry") as mock_get:
        assert list(adapter.fetch_awards(date(2026, 6, 1), date(2026, 6, 30))) == []
        assert list(adapter.fetch_contracts(date(2026, 6, 1), date(2026, 6, 30))) == []
    mock_get.assert_not_called()


# ------------------------------------------------------------------ #
# health_check                                                         #
# ------------------------------------------------------------------ #

def test_health_check_ok(adapter):
    with patch("src.adapters.kepco.get_with_retry",
               return_value=_mock_response(_fixture("notices_page1.xml"))):
        assert adapter.health_check() is True


def test_health_check_fail(adapter):
    with patch("src.adapters.kepco.get_with_retry", side_effect=ConnectionError):
        assert adapter.health_check() is False
