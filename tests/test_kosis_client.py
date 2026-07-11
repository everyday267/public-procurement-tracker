"""KosisClient (src/kosis.py) 단위 테스트 — 실측 픽스처·모킹 기반, 네트워크 없음.

축 구성(probe 실측): 종합/전문 = 발주기관별(C1)·공사규모별(C2)·월별(C3),
전기 = 공사규모별(C1)·발주기관별(C2). 월별은 분류축이므로 합계+월 중복 주의.
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.db import get_connection, ensure_schema
from src.kosis import (
    KOSIS_TABLES,
    KosisClient,
    KosisError,
    amount_to_krw,
    collect_kosis,
    dimension_labels,
    ge_threshold_amount,
    scale_agency_summary,
    scale_brackets,
    scale_lower_bound_eok,
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


def _load_into(client, conn, key, fixture):
    data = load_json_fixture("kosis", fixture)
    client.fetch_table = lambda *a, **k: data
    collect_kosis(conn, client, tables=[key])


# ── 키·파라미터 ──────────────────────────────────────────────────────────

def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("KOSIS_API_KEY", raising=False)
    with pytest.raises(ValueError):
        KosisClient()


def test_params_obj_levels_and_itm_join(client):
    params = client._params(KOSIS_TABLES["gen"], prd_se=None, num_periods=10,
                            start_prd=None, end_prd=None)
    assert params["objL1"] == params["objL2"] == params["objL3"] == "ALL"
    assert params["objL4"] == "" and params["objL8"] == ""
    assert params["itmId"] == "16365AAD2 16365AAB6"    # 공백 결합 → requests가 '+' 인코딩
    assert params["prdSe"] == "Y"
    assert params["newEstPrdCnt"] == 10


def test_params_period_range_overrides_count(client):
    params = client._params(KOSIS_TABLES["elec"], prd_se="M", num_periods=5,
                            start_prd="202401", end_prd="202406")
    assert params["prdSe"] == "M"
    assert params["objL1"] == params["objL2"] == "ALL" and params["objL3"] == ""
    assert params["startPrdDe"] == "202401" and params["endPrdDe"] == "202406"
    assert "newEstPrdCnt" not in params


# ── normalize (실측 구조) ─────────────────────────────────────────────────

def test_normalize_gen_axes_and_unit(client):
    data = load_json_fixture("kosis", "gen_a072.json")
    row = client.normalize(data[0], KOSIS_TABLES["gen"])
    assert row["industry"] == "종합"
    assert (row["c1_obj"], row["c1_nm"]) == ("발주기관별", "합계")
    assert (row["c2_obj"], row["c2_nm"]) == ("공사규모별", "합계")
    assert (row["c3_obj"], row["c3_nm"]) == ("월별", "합계")
    assert row["itm_nm"] == "금액" and row["unit_nm"] == "십억원"
    assert row["dt"] == 145900.8


def test_normalize_elec_two_levels(client):
    data = load_json_fixture("kosis", "elec_a010.json")
    row = client.normalize(data[0], KOSIS_TABLES["elec"])
    assert (row["c1_obj"], row["c1_nm"]) == ("공사규모별", "5백만원미만")
    assert (row["c2_obj"], row["c2_nm"]) == ("발주기관별", "합계")
    assert row["c3_obj"] is None and row["c3_code"] == ""


# ── 단위 환산 ────────────────────────────────────────────────────────────

def test_amount_to_krw_units():
    assert amount_to_krw(145900.8, "십억원") == 145900.8 * 1_000_000_000
    assert amount_to_krw(108584992, "백만원") == 108584992 * 1_000_000
    assert amount_to_krw(100, "건") is None       # 비금액 단위
    assert amount_to_krw(None, "십억원") is None


# ── 공사규모 구간 하한 파싱 ──────────────────────────────────────────────

@pytest.mark.parametrize("label,expected", [
    ("100억원이상", 100.0),
    ("100억~300억", 100.0),
    ("300억원 이상", 300.0),
    ("1000억이상", 1000.0),
    ("50억~100억", 50.0),
    ("50~100억", 50.0),
    ("1억~3억", 1.0),
    ("4000만원 미만", 0.0),
    ("5백만원미만", 0.0),
    ("5백만원이상", 0.05),
    ("합계", None),
    ("계", None),
])
def test_scale_lower_bound_eok(label, expected):
    assert scale_lower_bound_eok(label) == expected


# ── 오류 처리 ────────────────────────────────────────────────────────────

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

def test_collect_kosis_idempotent_and_partial(client, db_conn, monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)   # 재시도 대기 제거
    gen = load_json_fixture("kosis", "gen_a072.json")
    elec = load_json_fixture("kosis", "elec_a010.json")

    def fake_fetch(table, prd_se=None, num_periods=10, start_prd=None, end_prd=None):
        if table.key == "gen":
            return gen
        if table.key == "elec":
            return elec
        raise KosisError("spec 표 조회 실패")           # 부분 실패 (재시도 후 포기)

    client.fetch_table = fake_fetch
    n1 = collect_kosis(db_conn, client, tables=["gen", "spec", "elec"])
    n2 = collect_kosis(db_conn, client, tables=["gen", "spec", "elec"])
    assert n1 == n2 == 8                                 # gen4 + elec4 (spec 실패)
    count = db_conn.execute("SELECT COUNT(*) FROM kosis_stats").fetchone()[0]
    assert count == 8                                    # 멱등


def test_fetch_table_resilient_retries_transient_error(client, monkeypatch):
    import time
    from src.kosis import _fetch_table_resilient
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    gen = load_json_fixture("kosis", "gen_a072.json")

    calls = {"n": 0}

    def flaky(table, prd_se=None, num_periods=10):
        calls["n"] += 1
        if calls["n"] < 3:                               # 처음 2회 오탐 오류
            raise KosisError("필수요청변수값이 누락되었습니다. (objL)")
        return gen

    client.fetch_table = flaky
    out = _fetch_table_resilient(client, KOSIS_TABLES["gen"], None, 10)
    assert len(out) == 4 and calls["n"] == 3


def test_fetch_table_resilient_falls_back_to_one_period(client, monkeypatch):
    import time
    from src.kosis import _fetch_table_resilient
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    gen = load_json_fixture("kosis", "gen_a072.json")

    seen = []

    def only_one_period(table, prd_se=None, num_periods=10):
        seen.append(num_periods)
        if num_periods != 1:
            raise KosisError("필수요청변수값이 누락되었습니다. (objL)")
        return gen

    client.fetch_table = only_one_period
    out = _fetch_table_resilient(client, KOSIS_TABLES["gen"], None, 10, attempts=2)
    assert len(out) == 4 and seen[-1] == 1               # 축소 재시도로 성공


# ── 축 매핑 + 월별 중복 방지 ─────────────────────────────────────────────

def test_dimension_labels(client, db_conn):
    _load_into(client, db_conn, "gen", "gen_a072.json")
    labels = dimension_labels(db_conn, industry="종합")
    assert "공사규모별" in labels and "발주기관별" in labels and "월별" in labels


def test_summary_excludes_month_double_count(client, db_conn):
    _load_into(client, db_conn, "gen", "gen_a072.json")
    # 금액 요약, 기본(month=None) → 월 합계 행만: 합계/합계, 합계/4000만원미만
    rows = scale_agency_summary(db_conn, "종합", itm_nm_like="금액")
    scales = sorted(r["scale"] for r in rows)
    assert scales == ["4000만원 미만", "합계"]            # 1월 행 제외됨
    tot = next(r for r in rows if r["scale"] == "합계")
    assert tot["krw"] == 145900.8 * 1_000_000_000        # 십억원 환산
    assert tot["agency"] == "합계" and tot["month"] == "합계"

    # 특정 월 지정
    jan = scale_agency_summary(db_conn, "종합", itm_nm_like="금액", month="1월")
    assert len(jan) == 1 and jan[0]["dt"] == 17773


def test_elec_summary_scale_from_c1(client, db_conn):
    _load_into(client, db_conn, "elec", "elec_a010.json")
    rows = scale_agency_summary(db_conn, "전기", itm_nm_like="금액")
    gov = next(r for r in rows if r["agency"] == "정부기관")
    assert gov["scale"] == "5백만원미만"
    assert gov["krw"] == 30142 * 1_000_000               # 백만원 환산


# ── 100억↑ 집계 ─────────────────────────────────────────────────────────

def test_ge_threshold_amount(db_conn):
    # 실 라벨 구조에 맞춘 합성 종합건설 행 (100억↑ 집계 검증)
    rows = [
        ("발주기관별", "국가기관", "공사규모별", "100억~300억", "금액", "십억원", 500.0),
        ("발주기관별", "국가기관", "공사규모별", "1000억원이상", "금액", "십억원", 200.0),
        ("발주기관별", "국가기관", "공사규모별", "50~100억원", "금액", "십억원", 999.0),
        ("발주기관별", "지방자치단체", "공사규모별", "100억~300억", "금액", "십억원", 300.0),
        ("발주기관별", "민간", "공사규모별", "300억원 이상", "금액", "십억원", 111.0),
        ("발주기관별", "국가기관", "공사규모별", "합계", "금액", "십억원", 9999.0),
    ]
    for i, (o1, m1, o2, m2, itm, unit, dt) in enumerate(rows):
        db_conn.execute(
            "INSERT INTO kosis_stats (org_id, tbl_id, industry, prd_de, itm_id, "
            "itm_nm, unit_nm, c1_obj, c1_code, c1_nm, c2_obj, c2_code, c2_nm, dt) "
            "VALUES ('365','T','종합','2024',?,?,?,?,?,?,?,?,?,?)",
            (str(i), itm, unit, o1, str(i), m1, o2, str(i), m2, dt),
        )
    db_conn.commit()

    # 전체 발주기관: 500+200+300+111 = 1111 십억원 (50~100억·합계 제외)
    allx = ge_threshold_amount(db_conn, "종합", min_eok=100)
    assert allx["krw"] == 1111.0 * 1_000_000_000
    assert allx["brackets"] == {"100억~300억", "1000억원이상", "300억원 이상"}

    # 공공만(국가기관+지자체): 500+200+300 = 1000 십억원
    pub = ge_threshold_amount(db_conn, "종합", min_eok=100,
                              agencies={"국가기관", "지방자치단체"})
    assert pub["krw"] == 1000.0 * 1_000_000_000
    assert pub["agencies"] == {"국가기관", "지방자치단체"}


def test_ge_threshold_cumulative_scheme_no_sum(db_conn):
    # 전기식 누적형('N억이상') — 합산 금지, '100억이상' 단일 구간이 곧 ≥100 총합
    from src.kosis import ge_threshold_amount
    rows = [
        ("공사규모별", "50억원이상", "발주기관별", "정부기관", 900.0),
        ("공사규모별", "100억원이상", "발주기관별", "정부기관", 500.0),
        ("공사규모별", "100억원이상", "발주기관별", "지방자치단체", 300.0),
    ]
    for i, (o1, m1, o2, m2, dt) in enumerate(rows):
        db_conn.execute(
            "INSERT INTO kosis_stats (org_id, tbl_id, industry, prd_de, itm_id, "
            "itm_nm, unit_nm, c1_obj, c1_code, c1_nm, c2_obj, c2_code, c2_nm, dt) "
            "VALUES ('370','T','전기','2024',?,?,'백만원',?,?,?,?,?,?,?)",
            (str(i), "금액", o1, str(i), m1, o2, str(i), m2, dt),
        )
    db_conn.commit()
    amt = ge_threshold_amount(db_conn, "전기", min_eok=100)
    assert amt["scheme"] == "cumulative"
    assert amt["brackets"] == {"100억원이상"}          # 50억이상 합산 안 함
    assert amt["krw"] == (500.0 + 300.0) * 1_000_000   # 두 발주기관 100억이상 합


def test_ge100_public_by_year_filters_public(db_conn):
    from src.kosis import ge100_public_by_year
    rows = [
        ("발주기관별", "정부기관", "공사규모별", "100~200억원 미만", "2024", 500.0),
        ("발주기관별", "민간", "공사규모별", "100~200억원 미만", "2024", 999.0),
        ("발주기관별", "지방자치단체", "공사규모별", "1000억원 이상", "2023", 200.0),
    ]
    for i, (o1, m1, o2, m2, yr, dt) in enumerate(rows):
        db_conn.execute(
            "INSERT INTO kosis_stats (org_id, tbl_id, industry, prd_de, itm_id, "
            "itm_nm, unit_nm, c1_obj, c1_code, c1_nm, c2_obj, c2_code, c2_nm, dt) "
            "VALUES ('365','T','종합',?,?,?,'십억원',?,?,?,?,?,?,?)",
            (yr, str(i), "금액", o1, str(i), m1, o2, str(i), m2, dt),
        )
    db_conn.commit()
    by_year = ge100_public_by_year(db_conn, "종합")
    # 민간 제외(공공만): 2024=정부 500, 2023=지자체 200 (십억원 환산)
    assert by_year["2024"] == 500.0 * 1_000_000_000
    assert by_year["2023"] == 200.0 * 1_000_000_000


def test_scale_brackets_sorted(db_conn):
    for i, nm in enumerate(["합계", "100억~300억", "4000만원 미만", "1000억원이상"]):
        db_conn.execute(
            "INSERT INTO kosis_stats (org_id, tbl_id, industry, prd_de, itm_id, "
            "itm_nm, unit_nm, c2_obj, c2_code, c2_nm, dt) "
            "VALUES ('365','T','종합','2024',?,?,'십억원','공사규모별',?,?,1.0)",
            (str(i), "금액", str(i), nm),
        )
    db_conn.commit()
    brackets = scale_brackets(db_conn, "종합")
    # 하한 오름차순, 합계(None)는 뒤로
    order = [b["scale"] for b in brackets]
    assert order.index("4000만원 미만") < order.index("100억~300억") < order.index("1000억원이상")
    assert order[-1] == "합계"
