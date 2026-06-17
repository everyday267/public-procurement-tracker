import sqlite3
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS agencies (
  agency_code  TEXT PRIMARY KEY,
  agency_name  TEXT,
  source_type  TEXT,
  phase        INT
);

CREATE TABLE IF NOT EXISTS notices (
  notice_id               TEXT PRIMARY KEY,
  source                  TEXT NOT NULL,
  notice_no               TEXT NOT NULL,
  agency_code             TEXT,
  title                   TEXT,
  work_type               TEXT DEFAULT '공사',
  construction_type       TEXT,
  bid_method              TEXT,
  is_long_term_continuing INTEGER DEFAULT 0,
  estimated_price         INTEGER,
  vat_included            INTEGER DEFAULT 0,
  bid_open_at             TEXT,
  posted_at               TEXT,
  status                  TEXT,
  zone_hq                 TEXT,
  license_conditions      TEXT,
  vendor_restrictions     TEXT,
  raw_payload             TEXT,
  source_hash             TEXT,
  collected_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS awards (
  award_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  source          TEXT,
  notice_no       TEXT NOT NULL,
  bidder_name     TEXT,
  bidder_biz_no   TEXT,
  award_price     INTEGER,
  award_rate      REAL,
  awarded_at      TEXT,
  winner_status   TEXT,
  expect_price    INTEGER,
  design_price    INTEGER,
  base_price      INTEGER,
  lot_num1        TEXT,
  lot_num2        TEXT,
  raw_payload     TEXT,
  collected_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contracts (
  contract_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  source          TEXT,
  notice_no       TEXT NOT NULL,
  contract_no     TEXT,
  contract_name   TEXT,
  contract_price  INTEGER,
  contracted_at   TEXT,
  contract_method TEXT,
  contractor_name TEXT,
  contractor_type TEXT,
  start_date      TEXT,
  end_date        TEXT,
  raw_payload     TEXT,
  collected_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS source_runs (
  run_id          TEXT PRIMARY KEY,
  source          TEXT,
  started_at      TEXT,
  ended_at        TEXT,
  status          TEXT,
  fetched_count   INTEGER DEFAULT 0,
  filtered_count  INTEGER DEFAULT 0,
  error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_notices_no     ON notices(notice_no);
CREATE INDEX IF NOT EXISTS idx_awards_no      ON awards(notice_no);
CREATE INDEX IF NOT EXISTS idx_contracts_no   ON contracts(notice_no);
CREATE INDEX IF NOT EXISTS idx_notices_posted ON notices(posted_at);
"""


def connect(db_path: str = "procurement.db") -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _serialize(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (dict, list)):
            out[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            out[k] = int(v)
        else:
            out[k] = v
    return out


def upsert_notices(conn: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    rows = [_serialize(r) for r in rows]
    cols = list(rows[0].keys())
    ph = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO notices ({','.join(cols)}) VALUES ({ph})"
    conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    conn.commit()
    return len(rows)


def insert_awards(conn: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    rows = [_serialize(r) for r in rows]
    cols = list(rows[0].keys())
    ph = ",".join(["?"] * len(cols))
    sql = f"INSERT INTO awards ({','.join(cols)}) VALUES ({ph})"
    conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    conn.commit()
    return len(rows)


def insert_contracts(conn: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    rows = [_serialize(r) for r in rows]
    cols = list(rows[0].keys())
    ph = ",".join(["?"] * len(cols))
    sql = f"INSERT INTO contracts ({','.join(cols)}) VALUES ({ph})"
    conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    conn.commit()
    return len(rows)
