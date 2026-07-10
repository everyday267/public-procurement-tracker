"""KisconClient (src/kiscon.py) 단위 테스트 — 픽스처·모킹 기반, 네트워크 없음."""
import os
import tempfile
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.db import get_connection, ensure_schema
from src.kiscon import (
    KisconClient,
    STAT_AMT_OP,
    collect_kiscon_stats,
    collect_kiscon_records,
)
from tests.helpers import load_json_fixture


@pytest.fixture
def client():
    return KisconClient(api_key="test-key")


@pytest.fixture
def db_conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = get_connection(path)
    ensure_schema(conn)
    yield conn
    conn.close()
    os.unlink(path)


# ── API 키 처리 ──────────────────────────────────────────────────────────

def test_api_key_unquoted_once():
    c = KisconClient(api_key="abc%2Bdef%3D%3D")
    assert c.api_key == "abc+def=="


def test_api_key_plain_unchanged():
    c = KisconClient(api_key="abc+def==")
    assert c.api_key == "abc+def=="


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("KISCON_API_KEY", raising=False)
    with pytest.raises(ValueError):
        KisconClient()


# ── normalize ───────────────────────────────────────────────────────────

def test_normalize_stat_amt_fixture(client):
    fixture = load_json_fixture("kiscon", "stat_amt.json")
    items = fixture["response"]["body"]["items"]["item"]

    row = client.normalize_stat(items[0], "amt")
    assert row["noti_date"] == "20260610"
    assert row["area_code"] == "11"
    assert row["balju_code"] == "0"
    assert row["dogub_code"] == "1"
    assert row["amt_100m"] == 1250.0
    assert row["area_name"] == "서울"

    # 쉼표 포함 금액 문자열 허용
    row2 = client.normalize_stat(items[1], "amt")
    assert row2["amt_100m"] == 2340.0


def test_normalize_stat_cnt(client):
    row = client.normalize_stat(
        {"cnt": "17", "notiDate": "20260610", "areaCode": "11",
         "baljuCode": "0", "dogubCode": "1"}, "cnt")
    assert row["cnt"] == 17
    assert "amt_100m" not in row


def test_normalize_record_field_candidates(client):
    raw = {
        "notiDate": "20260620", "areaCode": "11", "baljuCode": "0", "dogubCode": "1",
        "constNm": "OO지구 하수처리시설 설치공사", "cmpNm": "대한건설(주)",
        "contAmt": "15000000000", "startDate": "20260701", "endDate": "20281231",
    }
    row = client.normalize_record(raw)
    assert row["work_name"] == "OO지구 하수처리시설 설치공사"
    assert row["contractor_name"] == "대한건설(주)"
    assert row["contract_price"] == 15_000_000_000
    assert row["start_date"] == "20260701"
    assert row["record_key"]  # 내용 해시 존재

    # 같은 내용이면 같은 키 (멱등 upsert 보장)
    assert client.normalize_record(raw)["record_key"] == row["record_key"]


# ── 페이지네이션 ─────────────────────────────────────────────────────────

def _fake_response(items, total_count):
    resp = MagicMock()
    resp.json.return_value = {
        "response": {"body": {"totalCount": total_count,
                              "items": {"item": items}}}
    }
    return resp


def test_request_paginates_until_total(client):
    page1 = _fake_response([{"amt": 1, "notiDate": "20260601"},
                            {"amt": 2, "notiDate": "20260602"}], total_count=4)
    page2 = _fake_response([{"amt": 3, "notiDate": "20260603"},
                            {"amt": 4, "notiDate": "20260604"}], total_count=4)
    with patch("src.kiscon.get_with_retry", side_effect=[page1, page2]) as mock_get:
        items = list(client._request(STAT_AMT_OP, {"sDate": "20260601", "eDate": "20260630"}))
    assert len(items) == 4
    assert mock_get.call_count == 2
    # ServiceKey 케이싱·_type=json 확인
    query = mock_get.call_args_list[0][0][1]
    assert query["ServiceKey"] == "test-key"
    assert query["_type"] == "json"


def test_request_stops_on_empty_items(client):
    page1 = _fake_response([], total_count=100)  # totalCount와 불일치해도 안전 종료
    with patch("src.kiscon.get_with_retry", return_value=page1):
        items = list(client._request(STAT_AMT_OP, {}))
    assert items == []


def test_monthly_chunks_span(client):
    chunks = list(client._monthly_chunks(date(2026, 5, 15), date(2026, 7, 3)))
    assert chunks == [
        (date(2026, 5, 15), date(2026, 5, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2026, 7, 3)),
    ]


# ── 수집 (DB upsert 멱등) ────────────────────────────────────────────────

def test_collect_kiscon_stats_idempotent(client, db_conn):
    fixture = load_json_fixture("kiscon", "stat_amt.json")
    items = fixture["response"]["body"]["items"]["item"]

    def fake_fetch(operation, since, until, balju=None, dogub=None, area=None):
        if operation == STAT_AMT_OP:
            yield from items
        # StatCnt는 빈 응답 (스펙 미확정 시나리오)

    client.fetch_stats = fake_fetch
    cells = [("0", "1")]
    n1 = collect_kiscon_stats(db_conn, client, date(2026, 6, 1), date(2026, 6, 30), cells)
    n2 = collect_kiscon_stats(db_conn, client, date(2026, 6, 1), date(2026, 6, 30), cells)
    assert n1 == n2 == 3

    count = db_conn.execute("SELECT COUNT(*) FROM kiscon_stats").fetchone()[0]
    assert count == 3  # INSERT OR REPLACE 멱등

    total = db_conn.execute(
        "SELECT SUM(amt_100m) FROM kiscon_stats WHERE balju_code='0' AND dogub_code='1'"
    ).fetchone()[0]
    assert total == 1250 + 2340 + 87


def test_collect_records_skipped_without_op(client, db_conn):
    # 건별 오퍼레이션 미확정(records_op=None) → 수집 0건, 오류 없음
    client.records_op = None
    n = collect_kiscon_records(db_conn, client, date(2026, 6, 1), date(2026, 6, 30))
    assert n == 0
