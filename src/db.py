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
  notice_rev              INTEGER DEFAULT 0,
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

-- 공사 + 추정가격 미공개 건. notices와 동일한 컬럼을 격리 보관한다 (PRD 4장).
CREATE TABLE IF NOT EXISTS notices_unpriced (
  notice_id               TEXT PRIMARY KEY,
  source                  TEXT NOT NULL,
  notice_no               TEXT NOT NULL,
  notice_rev              INTEGER DEFAULT 0,
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

-- 공고 정정/변경 이력 (bidNtceOrd 등 notice_rev 기준). 조회용 스키마만 우선 마련.
CREATE TABLE IF NOT EXISTS notice_revisions (
  notice_id       TEXT NOT NULL,
  notice_no       TEXT NOT NULL,
  notice_rev      INTEGER DEFAULT 0,
  source          TEXT,
  title           TEXT,
  estimated_price INTEGER,
  source_hash     TEXT,
  detected_at     TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (notice_id, notice_rev)
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

-- 계약은 "체결일(cntrctCnclsDate) 기준"으로 독립 수집한다. 공고 매칭이 아니라
-- 공사(bsnsDivNm) + 계약금액 100억↑ 조건으로 직접 집계 → 공사이행보증서 대상
-- 계약 규모 파악. notice_no는 있으면 참고용으로 저장하되 조인 필수는 아니다.
CREATE TABLE IF NOT EXISTS contracts (
  contract_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source                TEXT,
  notice_no             TEXT,
  contract_no           TEXT,
  unity_contract_no     TEXT,
  contract_name         TEXT,
  bsns_div              TEXT,
  contract_price        INTEGER,
  total_contract_price  INTEGER,
  contracted_at         TEXT,
  contract_method       TEXT,
  contract_status       TEXT,
  is_long_term          TEXT,
  demand_inst           TEXT,
  contract_inst         TEXT,
  contractor_name       TEXT,
  contractor_bizno      TEXT,
  contractor_type       TEXT,
  contract_period       TEXT,
  start_date            TEXT,
  end_date              TEXT,
  raw_payload           TEXT,
  collected_at          TEXT DEFAULT (datetime('now'))
);

-- KISCON 건설공사대장 통보 통계 (ConStatInfoSvc StatAmt/StatCnt).
-- 일별 × 현장소재지 × 발주자구분 × 도급구분 셀. amt는 API 단위(억원) 그대로 저장.
CREATE TABLE IF NOT EXISTS kiscon_stats (
  noti_date    TEXT NOT NULL,
  area_code    TEXT NOT NULL,
  balju_code   TEXT NOT NULL,
  dogub_code   TEXT NOT NULL,
  amt_100m     REAL,
  cnt          INTEGER,
  area_name    TEXT,
  raw_payload  TEXT,
  collected_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (noti_date, area_code, balju_code, dogub_code)
);

-- KISCON 건별 통보 레코드 (건별 리스트 오퍼레이션 — probe로 스펙 확정 후 수집).
-- contract_price는 원 단위로 환산 저장한다 (환산 계수는 src/kiscon.py 참고).
CREATE TABLE IF NOT EXISTS kiscon_records (
  record_key      TEXT PRIMARY KEY,
  noti_date       TEXT,
  area_code       TEXT,
  balju_code      TEXT,
  dogub_code      TEXT,
  work_name       TEXT,
  contractor_name TEXT,
  contract_price  INTEGER,
  start_date      TEXT,
  end_date_plan   TEXT,
  raw_payload     TEXT,
  collected_at    TEXT DEFAULT (datetime('now')),
  UNIQUE (noti_date, work_name, contract_price)
);

-- KOSIS 통계 (건설협회 통계 OpenAPI). 종합/전문/전기 공사규모별·발주기관별
-- 계약실적. getList 응답을 long-format으로 저장한다. 분류축(공사규모/발주기관/
-- 지역 등)은 표마다 다르므로 C1~C3의 코드·값·축이름(obj)을 그대로 보관해
-- 검증 단계에서 이름으로 매핑한다. dt 단위는 unit_nm 참조(백만원·건 등).
CREATE TABLE IF NOT EXISTS kosis_stats (
  org_id       TEXT NOT NULL,
  tbl_id       TEXT NOT NULL,
  industry     TEXT,            -- 종합 | 전문 | 전기 (레지스트리 유래)
  prd_se       TEXT,            -- Y | M | Q
  prd_de       TEXT NOT NULL,   -- 기간 (YYYY 또는 YYYYMM)
  itm_id       TEXT NOT NULL,
  itm_nm       TEXT,
  unit_nm      TEXT,
  c1_obj       TEXT, c1_code TEXT, c1_nm TEXT,
  c2_obj       TEXT, c2_code TEXT, c2_nm TEXT,
  c3_obj       TEXT, c3_code TEXT, c3_nm TEXT,
  dt           REAL,
  raw_payload  TEXT,
  collected_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (org_id, tbl_id, itm_id, prd_de, c1_code, c2_code, c3_code)
);

-- KISCON 대조 결과. validate_kiscon이 기록하고 schema_monitor가 읽어 이슈화한다.
CREATE TABLE IF NOT EXISTS kiscon_recon (
  ym          TEXT NOT NULL,   -- 대상 월 YYYY-MM
  level       TEXT NOT NULL,   -- L0_AMT | L0_CNT | L2
  basis       TEXT NOT NULL,   -- contract_month | lag_adjusted
  ours_krw    INTEGER,
  kiscon_krw  INTEGER,
  ratio       REAL,
  n_ours      INTEGER,
  n_kiscon    INTEGER,
  n_matched   INTEGER,
  n_hat       INTEGER,         -- L2: Lincoln-Petersen 모집단 추정치
  flag        TEXT,            -- NULL | RATIO_GE_1 | OUT_OF_BAND | RATIO_JUMP | NO_KISCON_DATA
  detail      TEXT,
  computed_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (ym, level, basis)
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

CREATE INDEX IF NOT EXISTS idx_notices_no          ON notices(notice_no);
CREATE INDEX IF NOT EXISTS idx_notices_unpriced_no ON notices_unpriced(notice_no);
CREATE INDEX IF NOT EXISTS idx_awards_no           ON awards(notice_no);
CREATE INDEX IF NOT EXISTS idx_contracts_no        ON contracts(notice_no);
CREATE INDEX IF NOT EXISTS idx_contracts_date      ON contracts(contracted_at);
CREATE INDEX IF NOT EXISTS idx_contracts_inst      ON contracts(demand_inst);
CREATE INDEX IF NOT EXISTS idx_notices_posted      ON notices(posted_at);
CREATE INDEX IF NOT EXISTS idx_kiscon_stats_date   ON kiscon_stats(noti_date);
CREATE INDEX IF NOT EXISTS idx_kiscon_records_date ON kiscon_records(noti_date);
CREATE INDEX IF NOT EXISTS idx_kosis_industry     ON kosis_stats(industry, prd_de);
"""


def get_connection(db_path: str = "procurement.db") -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
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


def _upsert(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    rows = [_serialize(r) for r in rows]
    cols = list(rows[0].keys())
    ph = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({ph})"
    conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    conn.commit()
    return len(rows)


def upsert_notices(conn: sqlite3.Connection, rows: list[dict]) -> int:
    return _upsert(conn, "notices", rows)


def insert_unpriced_notices(conn: sqlite3.Connection, rows: list[dict]) -> int:
    return _upsert(conn, "notices_unpriced", rows)


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


def upsert_kiscon_stats(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """KISCON 집계 셀 upsert. PK(일자·지역·발주자·도급) 기준 멱등 — 재수집 시
    지연 통보가 반영된 최신 값으로 교체된다."""
    return _upsert(conn, "kiscon_stats", rows)


def upsert_kiscon_records(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """KISCON 건별 레코드 upsert. record_key 기준 멱등."""
    return _upsert(conn, "kiscon_records", rows)


def upsert_kosis_stats(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """KOSIS 통계 upsert. (org·tbl·itm·기간·분류코드) 기준 멱등."""
    return _upsert(conn, "kosis_stats", rows)


def upsert_kiscon_recon(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """KISCON 대조 결과 upsert. (ym, level, basis) 기준 — 재검증 시 교체."""
    return _upsert(conn, "kiscon_recon", rows)


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
