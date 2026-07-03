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
