"""KWaterAdapter 유닛 테스트 (Phase 2 Wave A).

3차 조사(run #19) 캡처 기반 fixture로 목록 파싱 / normalize / 페이지네이션 /
필터를 검증한다. 금액 필드는 실서비스 확정 전 후보 매핑(_PRICE_KEYS).
"""
import json
import pytest
from datetime import date
from unittest.mock import patch

from src.adapters.kwater import KWaterAdapter, PAGE_SIZE
from src.adapters.base import CONSTRUCTION_MIN_PRICE
from tests.helpers import load_json_fixture


@pytest.fixture
def adapter():
    a = KWaterAdapter()
    a.request_interval = 0
    return a


@pytest.fixture
def fixture_resp():
    return load_json_fixture("kwater")


# ------------------------------------------------------------------ #
# fetch — 요청 페이로드 / 페이지네이션                                  #
# ------------------------------------------------------------------ #

def test_fetch_notices_single_page(adapter, fixture_resp):
    with patch.object(adapter, "post_json", return_value=fixture_resp) as mock_post:
        rows = list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))
    assert len(rows) == 3
    assert mock_post.call_count == 1
    url, payload = mock_post.call_args.args
    assert url.endswith("retrievePaginatedBidPblancList.do")
    sd = payload["dmaSearchData"]
    assert sd["tndrPblancStartDe"] == "20260601"
    assert sd["tndrPblancEndDe"] == "20260630"
    assert sd["recordCountPerPage"] == PAGE_SIZE
    assert payload["ktagTokenField"] == "BID_savedToken"


def test_fetch_notices_paginates(adapter, fixture_resp):
    """totalCount가 페이지 크기보다 크면 pageIndex를 올려가며 재호출."""
    page1 = json.loads(json.dumps(fixture_resp))
    page2 = json.loads(json.dumps(fixture_resp))
    # 1페이지 102행 + 2페이지 3행 = totalCount 105 → 정확히 2회 호출
    page1["data"]["list"] = page1["data"]["list"] * 34  # 3*34=102
    page1["data"]["pagination"]["totalCount"] = 105
    page2["data"]["pagination"]["totalCount"] = 105
    with patch.object(adapter, "post_json", side_effect=[page1, page2]) as mock_post:
        list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))
    assert mock_post.call_count == 2
    assert mock_post.call_args_list[1].args[1]["dmaSearchData"]["pageIndex"] == 2


def test_fetch_notices_error_code_raises(adapter):
    bad = {"message": {"code": "fail", "code_name": "오류"}}
    with patch.object(adapter, "post_json", return_value=bad):
        with pytest.raises(RuntimeError, match="목록 XHR 오류"):
            list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))


def test_awards_contracts_empty(adapter):
    assert list(adapter.fetch_awards(date(2026, 6, 1), date(2026, 6, 30))) == []
    assert list(adapter.fetch_contracts(date(2026, 6, 1), date(2026, 6, 30))) == []


# ------------------------------------------------------------------ #
# normalize                                                            #
# ------------------------------------------------------------------ #

def test_normalize_construction(adapter, fixture_resp):
    raw = fixture_resp["data"]["list"][1]  # 350억 종합공사, 장기계속
    n = adapter.normalize(raw)
    assert n["source"] == "kwater"
    assert n["notice_id"] == "kwater:B5202602100:1"
    assert n["agency_code"] == "KWATER"
    assert n["work_type"] == "공사"
    assert n["construction_type"] == "종합"
    assert n["is_long_term_continuing"] is True
    assert n["bid_method"] == "일반경쟁"
    assert n["estimated_price"] == 35_000_000_000
    assert n["posted_at"] == "2026-06-15"
    assert n["status"] == "공고중"
    assert n["source_hash"]
    assert adapter.passes_filter(n) is True


def test_normalize_service_excluded(adapter, fixture_resp):
    n = adapter.normalize(fixture_resp["data"]["list"][0])  # 용역
    assert n["work_type"] == "용역"
    assert adapter.passes_filter(n) is False


def test_normalize_unpriced_construction(adapter, fixture_resp):
    n = adapter.normalize(fixture_resp["data"]["list"][2])  # presmtPc="-"
    assert n["work_type"] == "공사"
    assert n["construction_type"] == "전문"
    assert n["estimated_price"] is None
    assert adapter.is_unpriced(n) is True


def test_price_vat_conversion(adapter):
    n = adapter.normalize({"tndrPbanno": "B1", "tndrPblancNm": "공사",
                           "cntrctDivNm": "공사", "bdgtAmt": "11000000000",
                           "vatYn": "Y"})
    assert n["vat_included"] is True
    assert n["estimated_price"] == int(11_000_000_000 / 1.1)


def test_health_check_ok(adapter, fixture_resp):
    with patch.object(adapter, "post_json", return_value=fixture_resp):
        assert adapter.health_check() is True


def test_health_check_fail(adapter):
    with patch.object(adapter, "post_json", side_effect=ConnectionError):
        assert adapter.health_check() is False
