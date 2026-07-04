"""ScraperBaseAdapter 유닛 테스트 (실행계획 §3.3, M2-0)."""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from src.adapters.scraper_base import ScraperBaseAdapter, USER_AGENTS


class DummyScraper(ScraperBaseAdapter):
    source = "dummy"
    agency_codes = ["DUMMY"]

    def fetch_list_pages(self, since, until):
        yield {"rows": [{"no": "1"}, {"no": "2"}]}
        yield {"rows": [{"no": "3"}]}

    def parse_rows(self, page_payload):
        return page_payload["rows"]

    def normalize(self, raw):
        return {"notice_no": raw["no"], "work_type": "공사"}


@pytest.fixture
def adapter():
    a = DummyScraper()
    a.request_interval = 0  # 테스트에서는 대기 없음
    return a


def _resp(text: str, json_data=None) -> MagicMock:
    r = MagicMock()
    r.text = text
    if json_data is not None:
        r.json.return_value = json_data
    else:
        r.json.side_effect = ValueError("not json")
    return r


def test_fetch_notices_iterates_pages_and_rows(adapter):
    rows = list(adapter.fetch_notices(date(2026, 6, 1), date(2026, 6, 30)))
    assert [r["no"] for r in rows] == ["1", "2", "3"]


def test_awards_contracts_default_empty(adapter):
    assert list(adapter.fetch_awards(date(2026, 6, 1), date(2026, 6, 30))) == []
    assert list(adapter.fetch_contracts(date(2026, 6, 1), date(2026, 6, 30))) == []


def test_user_agent_rotation(adapter):
    uas = [adapter._headers()["User-Agent"] for _ in range(len(USER_AGENTS) + 1)]
    assert uas[0] != uas[1]              # 로테이션 동작
    assert uas[len(USER_AGENTS)] == uas[0]  # 한 바퀴 순환


def test_get_json_ok(adapter):
    with patch("src.adapters.scraper_base.get_with_retry",
               return_value=_resp("{}", {"data": []})):
        assert adapter.get_json("http://x/api") == {"data": []}


def test_get_json_non_json_raises(adapter):
    with patch("src.adapters.scraper_base.get_with_retry",
               return_value=_resp("<html>blocked</html>")):
        with pytest.raises(RuntimeError, match="비JSON"):
            adapter.get_json("http://x/api")


def test_get_passes_interval_and_ua(adapter):
    adapter.request_interval = 2.5
    with patch("src.adapters.scraper_base.get_with_retry",
               return_value=_resp("ok", {})) as mock_get:
        adapter.get_text("http://x/page", {"p": 1})
    kwargs = mock_get.call_args.kwargs
    assert kwargs["sleep_before"] == 2.5
    assert "User-Agent" in kwargs["headers"]
    assert kwargs["label"] == "dummy"


def test_common_helpers(adapter):
    assert adapter._to_int("1,234원") == 1234
    assert adapter._to_int("-") is None
    assert adapter._clean(" - ") is None
    assert adapter._parse_dt("20260601090000") == "2026-06-01T09:00:00"
    assert adapter._parse_dt("2026.06.01") == "2026-06-01"
    assert adapter._hash({"a": 1}) == adapter._hash({"a": 1})
