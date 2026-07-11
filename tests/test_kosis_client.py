"""KosisClient (src/kosis.py) 단위 테스트 — 픽스처·모킹 기반, 네트워크 없음."""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.db import get_connection, ensure_schema
from src.kosis import (
    KOSIS_TABLES,
    KosisClient,
    KosisError,
    collect_kosis,
    dimension_labels,
    scale_agency_summary,
)
from tests.helpers import load_json_fixture


@pytest.fixture
def client():
    return KosisClient(api_key="test-key")


@pytest.fixture
def db_conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = get_connection(path)
    ensure_schema(conn)
    yield conn
    conn.close()
    os.unlink(path)


# ── 키·파라미터 ──────────────────────────────────────────────────────────

def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("KOSIS_API_KEY", raising=False)
    with pytest.raises(ValueError):
        KosisClient()


def test_params_obj_levels_and_itm_join(client):
    params = client._params(KOSIS_TABLES["gen"], prd_se=None, num_periods=10,
                            start_prd=None, end_prd=None)
    # 3레벨 표: objL1~3=ALL, objL4~8=빈값
    assert params["objL1"] == params["objL2"] == params["objL3"] == "ALL"
    assert params["objL4"] == "" and params["objL8"] == ""
    # itmId는 공백 결합 (requests가 '+'로 인코딩)
    assert params["itmId"] == "16365AAD2 16365AAB6"
    assert params["prdSe"] == "Y"          # 표 등록 기본값
    assert params["newEstPrdCnt"] == 10
    assert "startPrdDe" not in params


def test_params_prd_se_override_and_period_range(client):
    params = client._params(KOSIS_TABLES["elec"], prd_se="M", num_periods=5,
                            start_prd="202401", end_prd="202406")
    assert params["prdSe"] == "M"          # override
    assert params["objL1"] == params["objL2"] == "ALL"
    assert params["objL3"] == ""            # 2레벨 표
    # 기간 범위를 주면 newEstPrdCnt 대신 startPrdDe/endPrdDe
    assert params["startPrdDe"] == "202401" and params["endPrdDe"] == "202406"
    assert "newEstPrdCnt" not in params


# ── normalize ───────────────────────────────────────────────────────────

def test_normalize_gen_fixture(client):
    data = load_json_fixture("kosis", "gen_a072.json")
    row = client.normalize(data[0], KOSIS_TABLES["gen"])
    assert row["industry"] == "종합"
    assert row["prd_de"] == "2024"
    assert row["itm_nm"] == "계약액"
    assert row["unit_nm"] == "백만원"
    assert row["c1_obj"] == "공사규모별" and row["c1_nm"] == "100억원이상"
    assert row["c2_obj"] == "발주자별" and row["c2_nm"] == "국가기관"
    assert row["c3_obj"] == "지역별"
    assert row["dt"] == 1234567.0          # 쉼표 파싱

    # DT='-' → None
    row_dash = client.normalize(data[3], KOSIS_TABLES["gen"])
    assert row_dash["dt"] is None


def test_normalize_elec_two_levels(client):
    data = load_json_fixture("kosis", "elec_a010.json")
    row = client.normalize(data[0], KOSIS_TABLES["elec"])
    assert row["industry"] == "전기"
    assert row["c2_obj"] == "발주기관별"
    assert row["c3_obj"] is None and row["c3_code"] == ""   # 3레벨 없음


# ── fetch 오류 처리 ──────────────────────────────────────────────────────

def _resp(json_value):
    r = MagicMock()
    r.json.return_value = json_value
    return r


def test_fetch_table_raises_on_error_object(client):
    err = {"err": "030", "errMsg": "인증키가 유효하지 않습니다."}
    with patch("src.kosis.get_with_retry", return_value=_resp(err)):
        with pytest.raises(KosisError) as ei:
            client.fetch_table(KOSIS_TABLES["gen"])
    assert "유효하지 않" in str(ei.value)


def test_fetch_table_returns_array(client):
    data = load_json_fixture("kosis", "gen_a072.json")
    with patch("src.kosis.get_with_retry", return_value=_resp(data)):
        out = client.fetch_table(KOSIS_TABLES["gen"])
    assert len(out) == 4


# ── collect (멱등 + 부분 실패 허용) ──────────────────────────────────────

def test_collect_kosis_idempotent_and_partial(client, db_conn):
    gen = load_json_fixture("kosis", "gen_a072.json")
    elec = load_json_fixture("kosis", "elec_a010.json")

    def fake_fetch(table, prd_se=None, num_periods=10, start_prd=None, end_prd=None):
        if table.key == "gen":
            return gen
        if table.key == "elec":
            return elec
        raise KosisError("spec 표 조회 실패")   # 부분 실패 시나리오

    client.fetch_table = fake_fetch
    n1 = collect_kosis(db_conn, client, tables=["gen", "spec", "elec"])
    n2 = collect_kosis(db_conn, client, tables=["gen", "spec", "elec"])
    assert n1 == n2 == 6                     # spec 실패해도 gen4+elec2 진행

    count = db_conn.execute("SELECT COUNT(*) FROM kosis_stats").fetchone()[0]
    assert count == 6                        # 멱등 upsert (재실행해도 증가 없음)


# ── 요약/축 매핑 ─────────────────────────────────────────────────────────

def test_dimension_labels_and_scale_agency_summary(client, db_conn):
    gen = load_json_fixture("kosis", "gen_a072.json")
    client.fetch_table = lambda *a, **k: gen
    collect_kosis(db_conn, client, tables=["gen"])

    labels = dimension_labels(db_conn, industry="종합")
    assert "공사규모별" in labels and "발주자별" in labels
    assert "100억원이상" in labels["공사규모별"]

    summary = scale_agency_summary(db_conn, "종합", itm_nm_like="계약액")
    # 계약액 행만: 100억이상×국가, 100억이상×지자체, 100억미만×민간(dt=None)
    over_public = [r for r in summary
                   if r["scale"] == "100억원이상" and r["agency"] == "국가기관"]
    assert len(over_public) == 1
    assert over_public[0]["dt"] == 1234567.0
    assert over_public[0]["unit_nm"] == "백만원"
