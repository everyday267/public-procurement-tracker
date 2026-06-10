"""월간 배치 실행기.

사용법:
    python -m src.run_monthly --month 2026-05
    python -m src.run_monthly --month 2026-05 --db-path /path/to/procurement.db
"""
import argparse
import json
import uuid
from datetime import datetime

from dateutil.parser import parse as dt_parse
from dateutil.relativedelta import relativedelta

from src.db import ensure_schema, get_connection
from src.adapters.g2b import G2BAdapter


# ------------------------------------------------------------------ #
# 유틸                                                                 #
# ------------------------------------------------------------------ #

def month_window(month_str: str):
    """'2026-05' → (2026-05-01, 2026-05-31)"""
    start = dt_parse(month_str + "-01").date()
    end = (start + relativedelta(months=1)) - relativedelta(days=1)
    return start, end


# ------------------------------------------------------------------ #
# DB 적재                                                              #
# ------------------------------------------------------------------ #

_NOTICE_INSERT = """
INSERT OR REPLACE INTO notices (
    notice_id, source, notice_no, notice_rev, agency_code, title, work_type,
    construction_type, is_long_term_continuing, bid_method, estimated_price,
    vat_included, posted_at, bid_open_at, status, raw_payload, source_hash, collected_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_UNPRICED_INSERT = """
INSERT OR REPLACE INTO notices_unpriced (
    notice_id, source, notice_no, notice_rev, agency_code, title, work_type,
    construction_type, is_long_term_continuing, bid_method, estimated_price,
    vat_included, posted_at, bid_open_at, status, raw_payload, source_hash, collected_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _notice_values(n: dict, collected_at: str) -> tuple:
    return (
        n["notice_id"], n["source"], n["notice_no"], n["notice_rev"],
        n["agency_code"], n["title"], n["work_type"],
        n["construction_type"], int(bool(n["is_long_term_continuing"])),
        n["bid_method"], n["estimated_price"],
        int(bool(n["vat_included"])), n["posted_at"], n["bid_open_at"],
        n["status"], json.dumps(n["raw_payload"], ensure_ascii=False),
        n["source_hash"], collected_at,
    )


# ------------------------------------------------------------------ #
# 메인                                                                 #
# ------------------------------------------------------------------ #

def run(month_str: str, db_path: str = "procurement.db") -> dict:
    since, until = month_window(month_str)
    conn = get_connection(db_path)
    ensure_schema(conn)
    collected_at = datetime.utcnow().isoformat()
    run_id = str(uuid.uuid4())

    adapter = G2BAdapter()
    fetched = inserted = unpriced = 0

    conn.execute(
        "INSERT INTO source_runs (run_id, source, started_at, status) VALUES (?, ?, ?, ?)",
        (run_id, adapter.source, collected_at, "running"),
    )
    conn.commit()

    try:
        for raw in adapter.fetch_notices(since, until):
            fetched += 1
            normalized = adapter.normalize(raw)
            normalized["collected_at"] = collected_at

            if adapter.passes_filter(normalized):
                conn.execute(_NOTICE_INSERT, _notice_values(normalized, collected_at))
                inserted += 1
            elif adapter.is_unpriced(normalized):
                conn.execute(_UNPRICED_INSERT, _notice_values(normalized, collected_at))
                unpriced += 1

        conn.execute(
            "UPDATE source_runs SET ended_at=?, status=?, fetched_count=?, filtered_count=? WHERE run_id=?",
            (datetime.utcnow().isoformat(), "success", fetched, inserted, run_id),
        )
    except Exception as exc:
        conn.execute(
            "UPDATE source_runs SET ended_at=?, status=?, error_message=? WHERE run_id=?",
            (datetime.utcnow().isoformat(), "error", str(exc), run_id),
        )
        raise
    finally:
        conn.commit()
        conn.close()

    return {"month": month_str, "fetched": fetched, "inserted": inserted, "unpriced": unpriced}


def main():
    parser = argparse.ArgumentParser(description="나라장터 등 공공발주 월간 수집")
    parser.add_argument("--month", required=True, help="수집 월 (예: 2026-05)")
    parser.add_argument("--db-path", default="procurement.db")
    args = parser.parse_args()
    result = run(args.month, args.db_path)
    print(result)


if __name__ == "__main__":
    main()
