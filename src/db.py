import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agencies (
    agency_code      TEXT PRIMARY KEY,
    agency_name      TEXT,
    g2b_inst_cd      TEXT,
    source_type      TEXT,
    self_system_url  TEXT,
    phase            INTEGER
);

CREATE TABLE IF NOT EXISTS notices (
    notice_id                TEXT PRIMARY KEY,
    source                   TEXT,
    notice_no                TEXT,
    notice_rev               INTEGER,
    agency_code              TEXT,
    title                    TEXT,
    work_type                TEXT,
    construction_type        TEXT,
    is_long_term_continuing  INTEGER DEFAULT 0,
    bid_method               TEXT,
    estimated_price          INTEGER,
    vat_included             INTEGER DEFAULT 0,
    posted_at                TEXT,
    bid_open_at              TEXT,
    status                   TEXT,
    raw_payload              TEXT,
    source_hash              TEXT,
    collected_at             TEXT
);

CREATE TABLE IF NOT EXISTS notices_unpriced (
    notice_id                TEXT PRIMARY KEY,
    source                   TEXT,
    notice_no                TEXT,
    notice_rev               INTEGER,
    agency_code              TEXT,
    title                    TEXT,
    work_type                TEXT,
    construction_type        TEXT,
    is_long_term_continuing  INTEGER DEFAULT 0,
    bid_method               TEXT,
    estimated_price          INTEGER,
    vat_included             INTEGER DEFAULT 0,
    posted_at                TEXT,
    bid_open_at              TEXT,
    status                   TEXT,
    raw_payload              TEXT,
    source_hash              TEXT,
    collected_at             TEXT
);

CREATE TABLE IF NOT EXISTS awards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id    TEXT,
    bidder_name  TEXT,
    bidder_biz_no TEXT,
    award_price  INTEGER,
    award_rate   REAL,
    awarded_at   TEXT,
    raw_payload  TEXT
);

CREATE TABLE IF NOT EXISTS contracts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id       TEXT,
    contract_no     TEXT,
    contract_price  INTEGER,
    contracted_at   TEXT,
    contract_period TEXT,
    raw_payload     TEXT
);

CREATE TABLE IF NOT EXISTS notice_revisions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id      TEXT,
    rev_from       INTEGER,
    rev_to         INTEGER,
    changed_fields TEXT,
    changed_at     TEXT
);

CREATE TABLE IF NOT EXISTS source_runs (
    run_id         TEXT PRIMARY KEY,
    source         TEXT,
    started_at     TEXT,
    ended_at       TEXT,
    status         TEXT,
    fetched_count  INTEGER DEFAULT 0,
    filtered_count INTEGER DEFAULT 0,
    error_message  TEXT,
    response_hash  TEXT
);
"""


def get_connection(db_path: str = "procurement.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
