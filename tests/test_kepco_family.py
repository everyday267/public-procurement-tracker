"""발전 자회사 어댑터 5종 테스트 (Phase 2 Wave B).

KEPCOAdapter 재사용 구조 검증: companyId 고정, source/agency/notice_id 분리,
KEPCO_API_KEY 공유, run_monthly 등록.
"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from src.adapters.kepco_family import (
    GENCO_ADAPTERS, EWPAdapter, KOMIPOAdapter, KOENAdapter,
    KOSPOAdapter, KOWEPOAdapter,
)
from src.adapters.kepco import KEPCOAdapter
import src.run_monthly as rm
from tests.helpers import load_fixture

_EXPECTED = {
    "kowepo": ("KOWEPO", "COM02"),
    "kospo":  ("KOSPO",  "COM04"),
    "komipo": ("KOMIPO", "COM05"),
    "koen":   ("KOEN",   "COM06"),
    "ewp":    ("EWP",    "COM08"),
}


@pytest.mark.parametrize("name", list(GENCO_ADAPTERS))
def test_company_id_and_identity(name):
    agency, com = _EXPECTED[name]
    a = GENCO_ADAPTERS[name](service_key="k")
    assert a.source == name
    assert a.agency_codes == [agency]
    assert a.company_id == com
    assert isinstance(a, KEPCOAdapter)


def test_requires_kepco_api_key(monkeypatch):
    monkeypatch.delenv("KEPCO_API_KEY", raising=False)
    with pytest.raises(ValueError):
        EWPAdapter()


def test_fetch_uses_company_id():
    a = KOMIPOAdapter(service_key="k")
    resp = MagicMock()
    resp.text = load_fixture("kepco", "notices.json")
    with patch("src.adapters.kepco.get_with_retry", return_value=resp) as mock_get:
        rows = list(a.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))
    assert len(rows) == 4  # kepco fixture 재사용
    assert mock_get.call_args.args[1]["companyId"] == "COM05"


def test_normalize_source_override():
    """서브클래스 normalize가 자기 source/agency/notice_id 접두사를 써야 함."""
    a = KOENAdapter(service_key="k")
    n = a.normalize({"no": "K1", "name": "송전설비 보강공사",
                     "itemType": "Construction", "presumedPrice": "15000000000"})
    assert n["source"] == "koen"
    assert n["agency_code"] == "KOEN"
    assert n["notice_id"] == "koen:K1:1"
    assert n["estimated_price"] == 15_000_000_000


def test_kepco_normalize_unchanged():
    """리팩터 후에도 kepco 본체의 식별자는 그대로여야 함 (회귀 방지)."""
    a = KEPCOAdapter(service_key="k")
    n = a.normalize({"no": "E1", "name": "전력구공사", "itemType": "Construction"})
    assert n["source"] == "kepco"
    assert n["agency_code"] == "KEPCO"
    assert n["notice_id"] == "kepco:E1:1"


def test_registered_in_run_monthly():
    for name in GENCO_ADAPTERS:
        assert name in rm.SOURCES
        assert name in rm.SELF_SCOPED
