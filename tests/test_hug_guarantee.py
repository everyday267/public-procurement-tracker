"""HUG 분양보증현황 클라이언트 유닛 테스트.

사양서 기반 fixture로 XML 파싱 / 요청 파라미터 / 유형 분류 / CSV 출력을 검증한다.
"""
from unittest.mock import Mock, patch

import pytest
import requests

from src.hug_guarantee import (
    BASE_URL, CAT_APARTMENT, CAT_MIXED, CAT_OFFICETEL, CAT_RETAIL, CAT_UNKNOWN,
    HugApiError, HugGuaranteeClient, annotate, classify_item, parse_response,
    summarize, write_csv,
)
from tests.helpers import load_fixture


@pytest.fixture
def area_xml():
    return load_fixture("hug", "area_list.xml")


@pytest.fixture
def detail_xml():
    return load_fixture("hug", "detail.xml")


@pytest.fixture
def client():
    return HugGuaranteeClient(api_key="test-key", request_interval=0)


def _resp(text):
    return Mock(text=text)


# ------------------------------------------------------------------ #
# 파싱                                                                #
# ------------------------------------------------------------------ #

def test_parse_response_fields(area_xml):
    parsed = parse_response(area_xml)
    assert parsed.result_code == "00"
    assert parsed.result_msg == "NORMAL SERVICE."
    assert len(parsed.items) == 5
    first = parsed.items[0]
    assert first["BUNO"] == "017732"
    assert first["BSU_NM"] == "가화만사성 정관타운"
    assert first["CUST_NM1"] == "가화건설"
    assert first["CUST_NM2"] == "(주)가화건설"
    assert first["ADDR"].startswith("부산 기장군")


def test_parse_response_keeps_unknown_fields(detail_xml):
    """상세 응답 필드는 사양서 미명시 → 미지 태그도 그대로 보존해야 한다."""
    item = parse_response(detail_xml).items[0]
    assert item["HOUS_DCD_NM"] == "오피스텔"
    assert item["TOT_HSHLD_CNT"] == "240"


def test_parse_response_tolerates_unclosed_items_tag(area_xml):
    """사양서 예제처럼 items 닫는 태그가 빠져도 item을 찾아야 한다."""
    loose = area_xml.replace("</items>", "")
    assert len(parse_response(loose).items) == 5


# ------------------------------------------------------------------ #
# 요청                                                                #
# ------------------------------------------------------------------ #

def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("HUG_API_KEY", raising=False)
    with pytest.raises(ValueError):
        HugGuaranteeClient()


def test_fetch_area_params(client, area_xml):
    with patch("src.hug_guarantee.get_with_retry", return_value=_resp(area_xml)) as m:
        rows = client.fetch_area("02")
    url = m.call_args.args[0]
    params = m.call_args.args[1]
    assert url == BASE_URL
    assert params == {"API_KEY": "test-key", "AREA_DCD": "02"}
    assert len(rows) == 5
    assert all(r["AREA_DCD"] == "02" for r in rows)


def test_fetch_detail_uses_buno(client, detail_xml):
    with patch("src.hug_guarantee.get_with_retry", return_value=_resp(detail_xml)) as m:
        item = client.fetch_detail("019001")
    assert m.call_args.args[1] == {"API_KEY": "test-key", "BUNO": "019001"}
    assert item["HOUS_DCD_NM"] == "오피스텔"


def test_error_result_code_raises(client):
    err_xml = load_fixture("hug", "error.xml")
    with patch("src.hug_guarantee.get_with_retry", return_value=_resp(err_xml)):
        with pytest.raises(HugApiError) as e:
            client.fetch_area("02")
    assert e.value.result_code == "30"


def test_fetch_areas_skips_failing_codes(client, area_xml):
    err_xml = load_fixture("hug", "error.xml")
    with patch("src.hug_guarantee.get_with_retry",
               side_effect=[_resp(area_xml), _resp(err_xml), _resp(area_xml)]):
        rows = client.fetch_areas(["01", "02", "03"])
    assert len(rows) == 10          # 실패한 02는 건너뛰고 나머지 2개 지역만
    assert {r["AREA_DCD"] for r in rows} == {"01", "03"}


def test_fetch_areas_propagates_network_error(client):
    with patch("src.hug_guarantee.get_with_retry",
               side_effect=requests.ConnectionError("boom")):
        assert client.fetch_areas(["01"]) == []
        with pytest.raises(requests.ConnectionError):
            client.fetch_areas(["01"], skip_errors=False)


def test_enrich_with_detail_merges_without_overwriting(client, area_xml, detail_xml):
    with patch("src.hug_guarantee.get_with_retry", return_value=_resp(area_xml)):
        rows = client.fetch_area("02")
    with patch("src.hug_guarantee.get_with_retry", return_value=_resp(detail_xml)):
        merged = client.enrich_with_detail(rows, limit=1)
    assert merged[0]["HOUS_DCD_NM"] == "오피스텔"
    assert merged[0]["BSU_NM"] == "가화만사성 정관타운"   # 목록 값 유지
    assert "HOUS_DCD_NM" not in merged[1]                # limit=1 → 나머지는 그대로


# ------------------------------------------------------------------ #
# 유형 분류                                                            #
# ------------------------------------------------------------------ #

def test_classify_prefers_type_field_over_name():
    item = {"BSU_NM": "해운대 그랜드 아파트", "HOUS_DCD_NM": "오피스텔"}
    assert classify_item(item) == (CAT_OFFICETEL, "field:HOUS_DCD_NM")


def test_classify_falls_back_to_name_guess():
    assert classify_item({"BSU_NM": "개금역 스퀘어 오피스텔"}) == (CAT_OFFICETEL, "name")
    assert classify_item({"BSU_NM": "명지 센트럴파크 상가"}) == (CAT_RETAIL, "name")
    assert classify_item({"BSU_NM": "정관 e편한세상 아파트"}) == (CAT_APARTMENT, "name")
    assert classify_item({"BSU_NM": "부산역 주상복합"}) == (CAT_MIXED, "name")


def test_classify_unknown_when_no_keyword():
    assert classify_item({"BSU_NM": "해운대 그랜드 SK VIEW"}) == (CAT_UNKNOWN, "")


def test_classify_ignores_keyword_in_identity_fields():
    """시행사명에 '상가'가 있어도 '확정' 근거로 쓰지 않는다."""
    item = {"BSU_NM": "해운대 그랜드 SK VIEW", "CUST_NM1": "(주)대한상가개발"}
    assert classify_item(item) == (CAT_UNKNOWN, "")


def test_retail_wins_over_apartment_in_mixed_name():
    """'○○아파트 상가'는 상가로 분류돼야 한다."""
    assert classify_item({"BSU_NM": "정관 아파트 상가"})[0] == CAT_RETAIL


def test_annotate_and_summarize(area_xml):
    rows = annotate(parse_response(area_xml).items)
    counts = summarize(rows)
    assert counts[CAT_OFFICETEL] == 1
    assert counts[CAT_RETAIL] == 1
    assert counts[CAT_APARTMENT] == 1     # 도시형생활주택
    assert counts[CAT_UNKNOWN] == 2       # 가화만사성 정관타운 / 해운대 그랜드 SK VIEW
    assert rows[2]["category_label"] == "오피스텔"


# ------------------------------------------------------------------ #
# 출력                                                                #
# ------------------------------------------------------------------ #

def test_write_csv_includes_extra_detail_columns(tmp_path, area_xml):
    rows = annotate(parse_response(area_xml).items)
    rows[0]["HOUS_DCD_NM"] = "오피스텔"
    out = tmp_path / "sub" / "hug.csv"
    write_csv(rows, out)
    text = out.read_text(encoding="utf-8-sig")
    header = text.splitlines()[0]
    assert header.startswith("AREA_DCD,BUNO,BSU_NM,CUST_NM1,CUST_NM2,ADDR,"
                             "category,category_label,category_basis")
    assert header.endswith("HOUS_DCD_NM")
    assert len(text.strip().splitlines()) == 6


def test_dedupe_by_buno_keeps_first(area_xml):
    from src.hug_guarantee import dedupe_by_buno
    items = parse_response(area_xml).items
    rows = [{**i, "AREA_DCD": "02"} for i in items] + \
           [{**i, "AREA_DCD": "03"} for i in items]
    deduped = dedupe_by_buno(rows)
    assert len(deduped) == 5
    assert all(r["AREA_DCD"] == "02" for r in deduped)
