import json
import sqlite3
import tempfile
import os

import pytest

from src.db import get_connection, ensure_schema
from src.schema_monitor import find_problems


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = get_connection(path)
    ensure_schema(conn)
    yield path, conn
    conn.close()
    os.unlink(path)


def test_no_problems_when_all_success(db_path):
    path, conn = db_path
    conn.execute(
        "INSERT INTO source_runs (run_id, source, started_at, ended_at, status, fetched_count, filtered_count) "
        "VALUES ('r1','lh','2026-06-01T00:00:00','2026-06-01T00:01:00','success',10,5)"
    )
    conn.execute(
        "INSERT INTO notices (notice_id, source, notice_no, raw_payload) VALUES (?,?,?,?)",
        ("lh:1:1", "lh", "1", json.dumps({"bidNum": "1", "bidnmKor": "x", "presmtPrc": "1"})),
    )
    conn.commit()
    assert find_problems(path) == []


def test_flags_failed_run(db_path):
    path, conn = db_path
    conn.execute(
        "INSERT INTO source_runs (run_id, source, started_at, ended_at, status, error_message) "
        "VALUES ('r1','lh','2026-06-01T00:00:00','2026-06-01T00:01:00','error','boom')"
    )
    conn.commit()
    problems = find_problems(path)
    assert len(problems) == 1
    assert "실행 실패" in problems[0]["title"]


def test_kiscon_recon_flag_becomes_problem(db_path):
    path, conn = db_path
    conn.execute(
        "INSERT INTO kiscon_recon (ym, level, basis, ratio, flag, detail) "
        "VALUES ('2026-06','L0_AMT','lag_adjusted',1.2,'RATIO_GE_1','이중계상 의심')"
    )
    conn.commit()
    problems = find_problems(path)
    assert len(problems) == 1
    assert problems[0]["title"].startswith("[kiscon-validation] 2026-06")
    assert "RATIO_GE_1" in problems[0]["title"]
    assert problems[0]["label"] == "kiscon-validation"


def test_kiscon_recon_without_flag_is_silent(db_path):
    path, conn = db_path
    conn.execute(
        "INSERT INTO kiscon_recon (ym, level, basis, ratio, flag) "
        "VALUES ('2026-06','L0_AMT','lag_adjusted',0.4,NULL)"
    )
    conn.commit()
    assert find_problems(path) == []


def test_kiscon_recon_missing_table_no_crash(tmp_path):
    # kiscon_recon 테이블이 없는 구버전 DB에서도 죽지 않아야 한다
    import sqlite3 as _sqlite3
    old_db = str(tmp_path / "old.db")
    conn = _sqlite3.connect(old_db)
    conn.execute("CREATE TABLE source_runs (run_id TEXT PRIMARY KEY, source TEXT, "
                 "started_at TEXT, ended_at TEXT, status TEXT, fetched_count INT, "
                 "filtered_count INT, error_message TEXT)")
    conn.commit()
    conn.close()
    assert find_problems(old_db) == []


def test_flags_schema_drift(db_path):
    path, conn = db_path
    conn.execute(
        "INSERT INTO source_runs (run_id, source, started_at, ended_at, status) "
        "VALUES ('r1','lh','2026-06-01T00:00:00','2026-06-01T00:01:00','success')"
    )
    conn.execute(
        "INSERT INTO notices (notice_id, source, notice_no, raw_payload) VALUES (?,?,?,?)",
        ("lh:1:1", "lh", "1", json.dumps({"someOtherField": "x"})),
    )
    conn.commit()
    problems = find_problems(path)
    assert len(problems) == 1
    assert "스키마 변경 의심" in problems[0]["title"]
