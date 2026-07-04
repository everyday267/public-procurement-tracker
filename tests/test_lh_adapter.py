"""LHAdapter 유닛 테스트 (실행계획 §2.6, G-4 보강).

실제 API 호출 없이 fixture(tests/fixtures/lh/*.xml) 기반으로
XML 파싱 / normalize_notice·award·contract 매핑 / 필터 / 페이지네이션 검증.
"""
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

from src.adapters.lh import LHAdapter
from src.adapters.base import CONSTRUCTION_MIN_PRICE

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lh"


@pytest.fixture
def adapter():
    return LHAdapter(service_key="test-key")


def _fixture_root(name: str) -> ET.Element:
    return ET.fromstring((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _fixture_items(adapter, name: str) -> list:
    return adapter._items(_fixture_root(name))


def _mock_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


# ------------------------------------------------------------------ #
# 생성자 / XML 파싱                                                    #
# ------------------------------------------------------------------ #

def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("LH_API_KEY", raising=False)
    with pytest.raises(ValueError):
        LHAdapter()


def test_items_parses_fixture(adapter):
    items = _fixture_items(adapter, "notices.xml")
    assert len(items) == 2
    assert items[0]["bidNum"] == "2026-LH-0001"
    assert items[0]["presmtPrc"] == "12000000000"
    assert items[1]["presmtPrc"] == ""   # 빈 태그 → 빈 문자열


# ------------------------------------------------------------------ #
# normalize_notice                                                     #
# ------------------------------------------------------------------ #

def test_normalize_notice_basic(adapter):
    raw = _fixture_items(adapter, "notices.xml")[0]
    n = adapter.normalize_notice(raw)
    assert n["source"] == "lh"
    assert n["notice_id"] == "lh:2026-LH-0001:1"
    assert n["notice_no"] == "2026-LH-0001"
    assert n["title"].startswith("○○지구")
    assert n["work_type"] == "공사"
    assert n["construction_type"] == "종합"          # req1LicGbNm=종합
    assert n["is_long_term_continuing"] is True      # 공고명에 "장기계속"
    assert n["bid_method"] == "종합심사낙찰제"
    assert n["estimated_price"] == 12_000_000_000
    assert n["vat_included"] is True                 # addtTax > 0
    assert n["posted_at"] == "20260601"
    assert n["bid_open_at"] == "2026-06-20T14:00:00"
    assert n["status"] == "공고중"
    assert n["zone_hq"] == "경기남부"
    assert n["source_hash"]


def test_normalize_notice_license_and_restrictions(adapter):
    raw = _fixture_items(adapter, "notices.xml")[0]
    n = adapter.normalize_notice(raw)
    assert n["license_conditions"] == [{
        "seq": 1, "type": "종합", "group": "건축공사업",
        "condition": "단독", "licenses": ["건축공사업"],
    }]
    assert n["vendor_restrictions"] == ["지역제한(경기)"]


def test_normalize_notice_unpriced(adapter):
    """추정가격 빈값 → None (unpriced 격리 대상)."""
    raw = _fixture_items(adapter, "notices.xml")[1]
    n = adapter.normalize_notice(raw)
    assert n["estimated_price"] is None
    assert n["vat_included"] is False
    assert n["construction_type"] == "전문"
    assert n["is_long_term_continuing"] is False
    assert adapter.passes_filter(n) is False


def test_passes_filter_threshold(adapter):
    assert adapter.passes_filter({"estimated_price": CONSTRUCTION_MIN_PRICE}) is True
    assert adapter.passes_filter({"estimated_price": CONSTRUCTION_MIN_PRICE - 1}) is False
    assert adapter.passes_filter({"estimated_price": None}) is False


# ------------------------------------------------------------------ #
# normalize_award / normalize_contract                                 #
# ------------------------------------------------------------------ #

def test_normalize_award(adapter):
    raw = _fixture_items(adapter, "awards.xml")[0]
    a = adapter.normalize_award(raw)
    assert a["source"] == "lh"
    assert a["notice_no"] == "2026-LH-0001"
    assert a["bidder_name"] == "한국건설(주)"
    assert a["bidder_biz_no"] == "123-45-67890"
    assert a["award_price"] == 11_500_000_000
    assert a["award_rate"] == 95.83
    assert a["awarded_at"] == "2026-06-20T14:00:00"
    assert a["winner_status"] == "낙찰"
    assert a["expect_price"] == 12_000_000_000
    assert a["design_price"] == 12_100_000_000
    assert a["base_price"] == 11_900_000_000
    assert a["lot_num1"] == "3"


def test_normalize_contract(adapter):
    raw = _fixture_items(adapter, "contracts.xml")[0]
    c = adapter.normalize_contract(raw)
    assert c["source"] == "lh"
    assert c["notice_no"] == "2026-LH-0001"
    assert c["contract_name"] == "○○지구 아파트 건설공사 1공구"
    assert c["contract_price"] == 11_500_000_000
    assert c["contracted_at"] == "2026-07-01"
    assert c["contract_method"] == "장기계속"
    assert c["contractor_name"] == "한국건설(주)"
    assert c["contractor_type"] == "대표사"
    assert c["start_date"] == "2026-07-15"
    assert c["end_date"] == "2029-07-14"


# ------------------------------------------------------------------ #
# 페이지네이션 / fetch 파라미터                                        #
# ------------------------------------------------------------------ #

def _page_xml(items_xml: str, total: int) -> str:
    return f"""<response><header><resultCode>00</resultCode></header>
    <body><items>{items_xml}</items><totalCount>{total}</totalCount></body></response>"""


def test_paginate_two_pages(adapter):
    pages = [
        _mock_response(_page_xml("<item><bidNum>N1</bidNum></item>"
                                 "<item><bidNum>N2</bidNum></item>", 3)),
        _mock_response(_page_xml("<item><bidNum>N3</bidNum></item>", 3)),
    ]
    with patch("src.adapters.lh.get_with_retry", side_effect=pages) as mock_get:
        rows = list(adapter._paginate("OpenBidInfoList", {}, page_size=2))
    assert [r["bidNum"] for r in rows] == ["N1", "N2", "N3"]
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[1].args[1]["pageNo"] == 2


def test_fetch_notices_date_params(adapter):
    with patch("src.adapters.lh.get_with_retry",
               return_value=_mock_response(_page_xml("", 0))) as mock_get:
        list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))
    params = mock_get.call_args.args[1]
    assert params["tndrbidRegDtStart"] == "20260601"
    assert params["tndrbidRegDtEnd"] == "20260630"
    assert params["serviceKey"] == "test-key"


# ------------------------------------------------------------------ #
# health_check                                                         #
# ------------------------------------------------------------------ #

def test_health_check_ok(adapter):
    with patch("src.adapters.lh.get_with_retry",
               return_value=_mock_response(_page_xml("", 0))):
        assert adapter.health_check() is True


def test_health_check_fail(adapter):
    with patch("src.adapters.lh.get_with_retry", side_effect=ConnectionError):
        assert adapter.health_check() is False
