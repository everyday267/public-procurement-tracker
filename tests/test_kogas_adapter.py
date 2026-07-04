"""KOGASAdapter 유닛 테스트 (Phase 2 Wave A).

5~8차 조사 실캡처 마크업 기반 fixture로 목록/상세 파싱·기간 필터·normalize 검증.
"""
import pytest
from datetime import date
from unittest.mock import patch

from src.adapters.kogas import KOGASAdapter, _detail_field
from tests.helpers import load_fixture


@pytest.fixture
def adapter():
    a = KOGASAdapter()
    a.request_interval = 0
    return a


@pytest.fixture
def list_html():
    return load_fixture("kogas", "list_p1.html")


@pytest.fixture
def detail_html():
    return load_fixture("kogas", "detail.html")


# ------------------------------------------------------------------ #
# 목록 파싱                                                            #
# ------------------------------------------------------------------ #

def test_parse_rows(adapter, list_html):
    rows = list(adapter.parse_rows(list_html))
    assert len(rows) == 3
    r = rows[0]
    assert r["notice_code"] == "2026062909"
    assert r["bid_code"] == "001"
    assert r["round"] == "01"
    assert r["bid_type"] == "전자입찰"
    assert r["title"] == "2026년 매설배관 건전성 확보공사"
    assert r["bid_kind"] == "전자입찰"
    assert r["work_div"] == "공사"
    assert r["method"] == "제한경쟁"
    assert r["close_dt"] == "2026.07.13 10:00"
    assert r["open_dt"] == "2026.07.13 11:00"


def test_fetch_notices_period_filter_and_detail(adapter, list_html, detail_html):
    """공고번호 앞 8자리 기간 필터 + 기간 내 건만 상세 POST."""
    with patch.object(adapter, "_get_html", return_value=list_html), \
         patch.object(adapter, "_post_html", return_value=detail_html) as mock_post:
        rows = list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))
    # 6월 공고 2건(0629, 0630)만, 5월(0515) 제외
    assert [r["notice_code"] for r in rows] == ["2026062909", "2026063018"]
    assert mock_post.call_count == 2
    assert rows[0]["estm_price"] == "35,000,000,000"
    assert rows[0]["posted_dt"] == "2026.07.03 15:21"


def test_fetch_notices_without_detail(adapter, list_html):
    adapter.fetch_detail = False
    with patch.object(adapter, "_get_html", return_value=list_html), \
         patch.object(adapter, "_post_html") as mock_post:
        rows = list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))
    assert len(rows) == 2
    mock_post.assert_not_called()


def test_pagination_by_total_records(adapter, list_html):
    """Total Records : 30 → 15행/페이지 = 2페이지 조회."""
    list_html = list_html.replace("Total Records : 3&nbsp;", "Total Records : 30&nbsp;")
    with patch.object(adapter, "_get_html", return_value=list_html) as mock_get:
        list(adapter.fetch_list_pages(date(2026, 6, 1), date(2026, 6, 30)))
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[1].args[1]["page"] == 2
    assert mock_get.call_args_list[0].args[1]["worktype"] == "C"


# ------------------------------------------------------------------ #
# 상세 파싱                                                            #
# ------------------------------------------------------------------ #

def test_detail_fields(detail_html):
    assert _detail_field(detail_html, "추정가격") == "35,000,000,000"
    assert _detail_field(detail_html, "부가세") == "3,500,000,000"
    assert _detail_field(detail_html, "합계금액") == "38,500,000,000"
    assert _detail_field(detail_html, "공고일시") == "2026.07.03 15:21"
    assert _detail_field(detail_html, "입찰신청및입찰마감일시") == "2026.07.13 10:00"
    assert _detail_field(detail_html, "개찰일시") == "2026.07.13 11:00"


# ------------------------------------------------------------------ #
# normalize                                                            #
# ------------------------------------------------------------------ #

def test_normalize_construction(adapter, list_html, detail_html):
    with patch.object(adapter, "_get_html", return_value=list_html), \
         patch.object(adapter, "_post_html", return_value=detail_html):
        raw = next(iter(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30))))
    n = adapter.normalize(raw)
    assert n["source"] == "kogas"
    assert n["notice_id"] == "kogas:2026062909-001:1"
    assert n["notice_no"] == "2026062909001"
    assert n["agency_code"] == "KOGAS"
    assert n["work_type"] == "공사"
    assert n["bid_method"] == "제한경쟁"
    assert n["estimated_price"] == 35_000_000_000   # 추정가격(부가세 별도)
    assert n["vat_included"] is False
    assert n["posted_at"] == "2026-07-03"
    assert n["bid_open_at"] == "2026-07-13"
    assert n["status"] == "전자입찰"
    assert adapter.passes_filter(n) is True
    assert n["source_hash"]


def test_normalize_unpriced_when_detail_missing(adapter):
    """상세 실패 시 estimated_price=None → unpriced 격리."""
    raw = {"notice_code": "2026062909", "bid_code": "001", "round": "01",
           "bid_type": "전자입찰", "title": "테스트 공사", "bid_kind": "전자입찰",
           "work_div": "공사", "method": "일반경쟁", "posted_date": "20260629"}
    n = adapter.normalize(raw)
    assert n["estimated_price"] is None
    assert n["posted_at"] == "2026-06-29"
    assert adapter.is_unpriced(n) is True


def test_awards_contracts_empty(adapter):
    assert list(adapter.fetch_awards(date(2026, 6, 1), date(2026, 6, 30))) == []
    assert list(adapter.fetch_contracts(date(2026, 6, 1), date(2026, 6, 30))) == []


def test_health_check(adapter, list_html):
    with patch.object(adapter, "_get_html", return_value=list_html):
        assert adapter.health_check() is True
    with patch.object(adapter, "_get_html", side_effect=ConnectionError):
        assert adapter.health_check() is False
