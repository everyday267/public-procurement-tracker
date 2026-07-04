"""EXAdapter 유닛 테스트 (Phase 2 Wave A).

기술문서 기반 fixture로 계약 수집 / 파싱 / normalize_contract / 100억 필터 검증.
"""
import json
import pytest
from datetime import date
from unittest.mock import patch

from src.adapters.ex import EXAdapter, PAGE_SIZE
from src.run_monthly import _is_target_contract
from tests.helpers import load_json_fixture


@pytest.fixture
def adapter():
    a = EXAdapter(service_key="test-key10")
    a.request_interval = 0
    return a


@pytest.fixture
def fixture_resp():
    return load_json_fixture("ex", "contracts.json")


# ------------------------------------------------------------------ #
# 생성자 / fetch 파라미터                                              #
# ------------------------------------------------------------------ #

def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("EX_API_KEY", raising=False)
    with pytest.raises(ValueError):
        EXAdapter()


def test_fetch_contracts_params(adapter, fixture_resp):
    with patch.object(adapter, "get_json", return_value=fixture_resp) as mock_get:
        rows = list(adapter.fetch_contracts(date(2026, 6, 1), date(2026, 6, 30)))
    assert len(rows) == 3
    assert mock_get.call_count == 1
    url, params = mock_get.call_args.args
    assert url.endswith("elctPrcmCntrtOppubPrss")
    assert params["key"] == "test-key10"
    assert params["type"] == "json"
    assert params["sCntrtCntgDates"] == "20260601"
    assert params["eCntrtCntgDates"] == "20260630"
    assert params["numOfRows"] == PAGE_SIZE


def test_fetch_contracts_paginates(adapter, fixture_resp):
    page1 = json.loads(json.dumps(fixture_resp))
    page1["list"] = page1["list"] * 34   # 102행
    page1["count"] = 105
    page2 = json.loads(json.dumps(fixture_resp))
    page2["count"] = 105
    with patch.object(adapter, "get_json", side_effect=[page1, page2]) as mock_get:
        rows = list(adapter.fetch_contracts(date(2026, 6, 1), date(2026, 6, 30)))
    assert len(rows) == 105
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[1].args[1]["pageNo"] == 2


def test_notices_awards_empty(adapter):
    assert list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30))) == []
    assert list(adapter.fetch_awards(date(2026, 6, 1), date(2026, 6, 30))) == []


# ------------------------------------------------------------------ #
# _extract_rows — 응답 형태 방어                                       #
# ------------------------------------------------------------------ #

def test_extract_rows_unknown_list_key(adapter):
    rows, total = adapter._extract_rows(
        {"code": "SUCCESS", "count": "2",
         "elctPrcmCntrtOppubPrssList": [{"a": 1}, {"a": 2}]})
    assert len(rows) == 2
    assert total == 2


def test_extract_rows_bare_list(adapter):
    rows, total = adapter._extract_rows([{"a": 1}])
    assert rows == [{"a": 1}]
    assert total == 1


def test_extract_rows_error_code_raises(adapter):
    with pytest.raises(RuntimeError, match="API 오류"):
        adapter._extract_rows({"code": "ERROR-300", "message": "인증키 오류"})


def test_extract_rows_empty_success(adapter):
    rows, total = adapter._extract_rows({"code": "SUCCESS", "count": "0"})
    assert rows == []
    assert total == 0


# ------------------------------------------------------------------ #
# normalize_contract / 필터                                            #
# ------------------------------------------------------------------ #

def test_normalize_contract_construction(adapter, fixture_resp):
    c = adapter.normalize_contract(fixture_resp["list"][0])
    assert c["source"] == "ex"
    assert c["notice_no"] == "EX2026060001"
    assert c["contract_name"].startswith("○○고속도로")
    assert c["bsns_div"] == "공사"
    assert c["contract_price"] == 45_000_000_000
    assert c["contracted_at"] == "2026-06-15"
    assert c["contract_method"] == "제한경쟁"
    assert c["is_long_term"] == "장기계속"
    assert c["demand_inst"] == "한국도로공사"
    assert c["contractor_name"] == "대한건설(주)"
    assert c["contractor_bizno"] == "123-45-67890"
    assert _is_target_contract(c) is True


def test_is_large_construction_contract(adapter, fixture_resp):
    big_ct, small_ct, big_sv = fixture_resp["list"]
    assert adapter.is_large_construction_contract(big_ct) is True     # CT 450억
    assert adapter.is_large_construction_contract(small_ct) is False  # CT 32억
    assert adapter.is_large_construction_contract(big_sv) is False    # SV 150억


def test_service_contract_excluded_by_target_filter(adapter, fixture_resp):
    c = adapter.normalize_contract(fixture_resp["list"][2])  # 용역 150억
    assert c["bsns_div"] == "용역"
    assert _is_target_contract(c) is False


def test_normalize_reference(adapter, fixture_resp):
    n = adapter.normalize(fixture_resp["list"][0])
    assert n["notice_id"] == "ex:EX2026060001:1"
    assert n["agency_code"] == "EX"
    assert n["work_type"] == "공사"
    assert n["is_long_term_continuing"] is True
    assert n["source_hash"]


def test_health_check(adapter, fixture_resp):
    with patch.object(adapter, "get_json", return_value=fixture_resp):
        assert adapter.health_check() is True
    with patch.object(adapter, "get_json", side_effect=ConnectionError):
        assert adapter.health_check() is False
