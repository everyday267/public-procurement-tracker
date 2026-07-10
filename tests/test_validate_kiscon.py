"""validate_kiscon (src/validate_kiscon.py) 단위 테스트 — 합성 DB 기반."""
import os
import tempfile

import pytest

from src.db import get_connection, ensure_schema
from src.validate_kiscon import (
    RATIO_BAND,
    kiscon_monthly_totals,
    match_records,
    name_similarity,
    our_monthly_totals,
    reconcile_l0,
    reconcile_l2,
    run,
    work_tokens,
)


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = get_connection(path)
    ensure_schema(conn)
    yield path, conn
    conn.close()
    os.unlink(path)


def _insert_contract(conn, source, contract_no, price, contracted_at,
                     name="OO지구 하수처리시설 설치공사", status=None, bsns_div="공사"):
    conn.execute(
        "INSERT INTO contracts (source, contract_no, contract_name, bsns_div, "
        "contract_price, contracted_at, contract_status) VALUES (?,?,?,?,?,?,?)",
        (source, contract_no, name, bsns_div, price, contracted_at, status),
    )


def _insert_stat(conn, noti_date, amt_100m, cnt=None, area="11", balju="0", dogub="1"):
    conn.execute(
        "INSERT INTO kiscon_stats (noti_date, area_code, balju_code, dogub_code, "
        "amt_100m, cnt) VALUES (?,?,?,?,?,?)",
        (noti_date, area, balju, dogub, amt_100m, cnt),
    )


# ── 비교가능 모집단 (정의 정렬) ────────────────────────────────────────────

def test_universe_excludes_kr_rail_duplicate_and_variants(db):
    _, conn = db
    base = dict(price=15_000_000_000, contracted_at="2026-06-10")
    _insert_contract(conn, "g2b_opnstd", "C-1", **base)
    # kr_rail은 g2b_opnstd 재수집분 → 제외돼야 함
    _insert_contract(conn, "kr_rail", "C-1", **base)
    # 같은 소스 내 완전 중복 행 → DISTINCT로 1건
    _insert_contract(conn, "g2b_opnstd", "C-1", **base)
    # 변경계약 제외
    _insert_contract(conn, "g2b_opnstd", "C-2", 20_000_000_000, "2026-06-15", status="변경")
    # KISCON 대상외 업종 제외 (키워드)
    _insert_contract(conn, "g2b_opnstd", "C-3", 12_000_000_000, "2026-06-20",
                     name="OO변전소 전기공사")
    conn.commit()

    strict = our_monthly_totals(conn, strict=True)
    assert strict == {"2026-06": {"krw": 15_000_000_000, "n": 1}}

    # 제외 미적용(loose)은 전기공사 포함
    loose = our_monthly_totals(conn, strict=False)
    assert loose["2026-06"]["n"] == 2
    assert loose["2026-06"]["krw"] == 27_000_000_000


def test_kiscon_monthly_totals_public_prime_only(db):
    _, conn = db
    _insert_stat(conn, "20260610", 1000, cnt=50)
    _insert_stat(conn, "20260611", 500, cnt=30)
    _insert_stat(conn, "20260612", 999, balju="1")          # 민간 → 제외
    _insert_stat(conn, "20260613", 999, dogub="2")          # 하도급 → 제외
    conn.commit()

    totals = kiscon_monthly_totals(conn)
    assert totals == {"2026-06": {"krw": 1500 * 100_000_000, "cnt": 80}}


# ── L0 대조 ─────────────────────────────────────────────────────────────

def _l0_row(rows, ym, level, basis):
    return next(r for r in rows
                if r["ym"] == ym and r["level"] == level and r["basis"] == basis)


def test_l0_normal_ratio_no_flag():
    ours = {"2026-06": {"krw": 50_000_000_000, "n": 3}}
    kiscon = {"2026-06": {"krw": 100_000_000_000, "cnt": 200},
              "2026-07": {"krw": 100_000_000_000, "cnt": 180}}
    rows = reconcile_l0(ours, kiscon, ["2026-06"])

    lag = _l0_row(rows, "2026-06", "L0_AMT", "lag_adjusted")
    assert lag["kiscon_krw"] == 200_000_000_000  # m + m+1 합산
    assert lag["ratio"] == 0.25
    assert RATIO_BAND[0] <= lag["ratio"] <= RATIO_BAND[1]
    assert lag["flag"] is None

    cm = _l0_row(rows, "2026-06", "L0_AMT", "contract_month")
    assert cm["kiscon_krw"] == 100_000_000_000
    assert cm["flag"] is None  # 플래그는 lag_adjusted에서만


def test_l0_ratio_ge_1_flag():
    ours = {"2026-06": {"krw": 300_000_000_000, "n": 5}}
    kiscon = {"2026-06": {"krw": 100_000_000_000, "cnt": 10}}
    rows = reconcile_l0(ours, kiscon, ["2026-06"])
    assert _l0_row(rows, "2026-06", "L0_AMT", "lag_adjusted")["flag"] == "RATIO_GE_1"


def test_l0_out_of_band_flag():
    ours = {"2026-06": {"krw": 1_000_000_000, "n": 1}}
    kiscon = {"2026-06": {"krw": 100_000_000_000, "cnt": 10}}
    rows = reconcile_l0(ours, kiscon, ["2026-06"])
    assert _l0_row(rows, "2026-06", "L0_AMT", "lag_adjusted")["flag"] == "OUT_OF_BAND"


def test_l0_no_kiscon_data_flag():
    ours = {"2026-06": {"krw": 1_000_000_000, "n": 1}}
    rows = reconcile_l0(ours, {}, ["2026-06"])
    assert _l0_row(rows, "2026-06", "L0_AMT", "lag_adjusted")["flag"] == "NO_KISCON_DATA"


def test_l0_ratio_jump_flag():
    ours = {"2026-05": {"krw": 50_000_000_000, "n": 2},
            "2026-06": {"krw": 50_000_000_000, "n": 2}}
    kiscon = {"2026-05": {"krw": 100_000_000_000, "cnt": 10},
              "2026-06": {"krw": 100_000_000_000, "cnt": 10}}
    # 5월 lag = 50/(100+100)=0.25, 6월 lag(7월 없음) = 50/100=0.50 → |Δ|=0.25 > 0.20
    rows = reconcile_l0(ours, kiscon, ["2026-05", "2026-06"])
    assert _l0_row(rows, "2026-06", "L0_AMT", "lag_adjusted")["flag"] == "RATIO_JUMP"


# ── L2 건별 매칭 ─────────────────────────────────────────────────────────

def test_name_similarity_and_phase_tokens():
    a = "OO지구 하수처리시설 설치공사 제2공구"
    b = "OO지구 하수처리시설 설치공사"
    _, phase = work_tokens(a)
    assert phase == {"제2공구"}
    assert name_similarity(a, b) == 1.0  # 공구 토큰은 본문에서 분리


def test_match_records_cascade():
    ours = [
        {"contract_no": "C-1", "contract_name": "A지구 도로확장공사",
         "contract_price": 15_000_000_000, "contracted_at": "2026-06-10"},
        {"contract_no": "C-2", "contract_name": "B댐 보강공사",
         "contract_price": 30_000_000_000, "contracted_at": "2026-06-12"},
        {"contract_no": "C-3", "contract_name": "C항만 준설공사",
         "contract_price": 50_000_000_000, "contracted_at": "2026-06-20"},
    ]
    kiscon = [
        # C-1: 금액 완전일치 + 통보 10일 후 → 1차 확정매칭
        {"noti_date": "20260620", "work_name": "A지구 도로확장공사",
         "contract_price": 15_000_000_000},
        # C-2: 금액 0.1% 오차 + 공사명 동일 → 2차 확률매칭
        {"noti_date": "20260701", "work_name": "B댐 보강공사",
         "contract_price": 30_030_000_000},
        # 무관한 건
        {"noti_date": "20260625", "work_name": "D터널 공사",
         "contract_price": 99_000_000_000},
    ]
    matches, un_ours, un_kiscon = match_records(ours, kiscon)
    tiers = {c["contract_no"]: t for c, _, t in matches}
    assert tiers == {"C-1": "exact", "C-2": "fuzzy"}
    assert [c["contract_no"] for c in un_ours] == ["C-3"]
    assert len(un_kiscon) == 1


def test_match_rejects_noti_before_contract():
    ours = [{"contract_no": "C-1", "contract_name": "A공사",
             "contract_price": 15_000_000_000, "contracted_at": "2026-06-10"}]
    kiscon = [{"noti_date": "20260601",  # 계약일보다 앞선 통보 → 다른 건
               "work_name": "A공사", "contract_price": 15_000_000_000}]
    matches, un_ours, _ = match_records(ours, kiscon)
    assert matches == [] and len(un_ours) == 1


def test_reconcile_l2_lincoln_petersen():
    ours = [{"contract_no": f"C-{i}", "contract_name": f"공사{i}",
             "contract_price": (15 + i) * 1_000_000_000,
             "contracted_at": "2026-06-10"} for i in range(4)]
    kiscon = [{"noti_date": "20260620", "work_name": f"공사{i}",
               "contract_price": (15 + i) * 1_000_000_000} for i in range(2)]
    rows, un_ours, un_kiscon = reconcile_l2(ours, kiscon, ["2026-06"])
    assert len(rows) == 1
    r = rows[0]
    assert (r["n_ours"], r["n_kiscon"], r["n_matched"]) == (4, 2, 2)
    assert r["n_hat"] == 4  # 4×2/2
    assert len(un_ours) == 2 and un_kiscon == []


def test_reconcile_l2_empty_without_records():
    rows, un_ours, un_kiscon = reconcile_l2([{"contract_price": 1}], [], ["2026-06"])
    assert rows == [] and un_ours == [] and un_kiscon == []


# ── run() 통합 (skip_fetch) ──────────────────────────────────────────────

def test_run_end_to_end_skip_fetch(db, tmp_path):
    path, conn = db
    _insert_contract(conn, "g2b_opnstd", "C-1", 15_000_000_000, "2026-06-10")
    # KISCON 6·7월 합계 = 500억 → lag ratio 150/500 = 0.30 (정상 밴드)
    _insert_stat(conn, "20260615", 300, cnt=40)
    _insert_stat(conn, "20260705", 200, cnt=25)
    # 건별 레코드 1건 (완전일치 매칭 대상)
    conn.execute(
        "INSERT INTO kiscon_records (record_key, noti_date, area_code, balju_code, "
        "dogub_code, work_name, contract_price) VALUES (?,?,?,?,?,?,?)",
        ("k1", "20260620", "11", "0", "1", "OO지구 하수처리시설 설치공사",
         15_000_000_000),
    )
    conn.commit()

    exit_code = run(path, skip_fetch=True, output_dir=str(tmp_path))
    assert exit_code == 0  # 플래그 없음

    recon = conn.execute("SELECT * FROM kiscon_recon").fetchall()
    levels = {r["level"] for r in recon}
    assert {"L0_AMT", "L0_CNT", "L2"} <= levels

    l2 = next(r for r in recon if r["level"] == "L2")
    assert (l2["n_ours"], l2["n_kiscon"], l2["n_matched"], l2["n_hat"]) == (1, 1, 1, 1)

    files = {p.name for p in tmp_path.iterdir()}
    assert any(f.startswith("kiscon_recon_") and f.endswith(".csv") for f in files)
    assert any(f.startswith("kiscon_recon_") and f.endswith(".md") for f in files)


def test_run_flags_exit_code(db, tmp_path):
    path, conn = db
    _insert_contract(conn, "g2b_opnstd", "C-1", 15_000_000_000, "2026-06-10")
    # KISCON 없음 → NO_KISCON_DATA 플래그 → exit 1
    conn.commit()
    assert run(path, skip_fetch=True, output_dir=str(tmp_path)) == 1
