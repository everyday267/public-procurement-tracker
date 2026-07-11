"""validate_kosis (src/validate_kosis.py) 단위 테스트 — 합성 DB 기반."""
import os
import tempfile

import pytest

from src.db import get_connection, ensure_schema
from src.validate_kosis import our_annual_public_100eok, reconcile, run


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = get_connection(path)
    ensure_schema(conn)
    yield path, conn
    conn.close()
    os.unlink(path)


def _contract(conn, price, at, source="g2b_opnstd", name="OO도로 확장공사",
              status=None, bsns="공사"):
    conn.execute(
        "INSERT INTO contracts (source, contract_no, contract_name, bsns_div, "
        "contract_price, contracted_at, contract_status) VALUES (?,?,?,?,?,?,?)",
        (source, "C" + at + str(price), name, bsns, price, at, status),
    )


def _kosis_gen(conn, year, agency, scale, dt_10eok):
    conn.execute(
        "INSERT INTO kosis_stats (org_id, tbl_id, industry, prd_de, itm_id, itm_nm, "
        "unit_nm, c1_obj, c1_code, c1_nm, c2_obj, c2_code, c2_nm, c3_obj, c3_code, "
        "c3_nm, dt) VALUES ('365','T','종합',?,?,'금액','십억원','발주기관별',?,?,"
        "'공사규모별',?,?,'월별','A01','합계',?)",
        (year, agency + scale + year, agency, agency, agency + scale, scale, dt_10eok),
    )


def test_our_annual_excludes_variants(db):
    _, conn = db
    _contract(conn, 15_000_000_000, "2024-03-10")
    _contract(conn, 20_000_000_000, "2024-05-01")
    _contract(conn, 15_000_000_000, "2024-03-10", source="kr_rail")       # 중복 제외
    _contract(conn, 30_000_000_000, "2024-06-01", status="변경")          # 변경 제외
    _contract(conn, 12_000_000_000, "2024-07-01", name="OO 전기공사")      # 업종 제외
    conn.commit()
    ours = our_annual_public_100eok(conn, strict=True)
    assert ours == {"2024": 35_000_000_000}


def test_reconcile_flags():
    ours = {"2024": 100_000_000_000_000, "2023": 10_000_000_000_000,
            "2022": 5_000_000_000_000}
    kosis = {"2024": 90_000_000_000_000,        # ratio 1.11 → 정상
             "2023": 1_000_000_000_000}         # ratio 10 → RATIO_HIGH (2022 없음 → NO_KOSIS)
    rows = {r["year"]: r for r in reconcile(ours, kosis)}
    assert rows["2024"]["flag"] is None
    assert rows["2023"]["flag"] == "RATIO_HIGH"
    assert rows["2022"]["flag"] == "NO_KOSIS"


def test_reconcile_ratio_low():
    rows = reconcile({"2024": 1_000_000_000_000}, {"2024": 100_000_000_000_000})
    assert rows[0]["flag"] == "RATIO_LOW"       # ratio 0.01


def test_run_end_to_end(db, tmp_path):
    path, conn = db
    # 우리 2024 100억↑ 공공 = 500억
    _contract(conn, 30_000_000_000, "2024-03-10")
    _contract(conn, 20_000_000_000, "2024-08-10")
    # KOSIS 종합 2024: 공공(정부 300억 + 지자체 200억) + 민간 900억(제외) = 공공 500억
    _kosis_gen(conn, "2024", "정부기관", "100~200억원 미만", 30.0)   # 십억원=300억
    _kosis_gen(conn, "2024", "지방자치단체", "100~200억원 미만", 20.0)  # 200억
    _kosis_gen(conn, "2024", "민간", "1000억원 이상", 90.0)          # 공공 아님
    _kosis_gen(conn, "2024", "정부기관", "합계", 999.0)              # 합계 제외
    conn.commit()

    code = run(path, output_dir=str(tmp_path))
    row = conn.execute("SELECT * FROM kosis_recon WHERE year='2024'").fetchone()
    assert row["ours_krw"] == 50_000_000_000
    assert row["kosis_krw"] == 50_000_000_000        # 공공 300+200억, 민간·합계 제외
    assert abs(row["ratio"] - 1.0) < 1e-9
    assert row["flag"] is None and code == 0
    assert any(p.name.startswith("kosis_recon_") for p in tmp_path.iterdir())
