import sqlite3
import tempfile
import os
from src.db import ensure_schema, get_connection


def test_schema_creation():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        conn = get_connection(db_path)
        ensure_schema(conn)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        expected = {"agencies", "notices", "notices_unpriced", "awards", "contracts", "notice_revisions", "source_runs"}
        assert expected <= tables
        conn.close()
    finally:
        os.unlink(db_path)
