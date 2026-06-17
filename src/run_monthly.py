"""
run_monthly.py — LH 월간 수집 + bidNum 조인 + CSV 저장

사용법:
    python -m src.run_monthly --month 2026-05
    python -m src.run_monthly --month 2026-05 --db procurement.db
"""
import os
import uuid
import json
import logging
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from dateutil.relativedelta import relativedelta
import pandas as pd

from src.adapters.lh import LHAdapter, CONSTRUCTION_MIN_PRICE
from src.db import connect, init_db, upsert_notices, insert_awards, insert_contracts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_monthly")


# ── 기간 계산 ──────────────────────────────────────────────────────────────

def month_bounds(ym: str) -> tuple[date, date]:
    y, m = map(int, ym.split("-"))
    start = date(y, m, 1)
    end = (start + relativedelta(months=1)) - timedelta(days=1)
    return start, end


# ── 조인 ──────────────────────────────────────────────────────────────────

def join_all(notices: list[dict], awards: list[dict], contracts: list[dict]) -> pd.DataFrame:
    ndf = pd.DataFrame(notices)[[
        "notice_no", "title", "construction_type", "bid_method",
        "is_long_term_continuing", "estimated_price", "posted_at", "status",
        "zone_hq", "license_conditions", "vendor_restrictions",
    ]].drop_duplicates("notice_no")

    adf = pd.DataFrame(awards)
    if not adf.empty and "winner_status" in adf.columns:
        winners = adf[adf["winner_status"].str.contains("낙찰", na=False)]
        adf = winners.drop_duplicates("notice_no") if not winners.empty else adf.drop_duplicates("notice_no")
    elif not adf.empty:
        adf = adf.drop_duplicates("notice_no")
    if not adf.empty:
        adf = adf[["notice_no", "bidder_name", "bidder_biz_no",
                   "award_price", "award_rate", "expect_price"]].rename(columns={
            "bidder_name": "winner_name", "bidder_biz_no": "winner_biz_no",
        })
    else:
        adf = pd.DataFrame()

    cdf = pd.DataFrame(contracts)
    if not cdf.empty:
        cdf = cdf.drop_duplicates("notice_no")[[
            "notice_no", "contract_name", "contract_price",
            "contracted_at", "contractor_name", "start_date", "end_date",
        ]]

    merged = ndf
    if not adf.empty:
        merged = merged.merge(adf, on="notice_no", how="left")
    if not cdf.empty:
        merged = merged.merge(cdf, on="notice_no", how="left")
    return merged


# ── 메인 ──────────────────────────────────────────────────────────────────

def run(month: str, db_path: str = "procurement.db", output_dir: str = "output"):
    start, end = month_bounds(month)
    run_id = str(uuid.uuid4())[:8]
    started_at = datetime.now().isoformat()
    logger.info("=== LH 수집 시작 | %s ~ %s | run_id=%s ===", start, end, run_id)

    lh = LHAdapter()
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    init_db(conn)

    # 공고 수집
    logger.info("[1/3] 입찰공고 수집 중...")
    raw_notices = list(lh.fetch_notices(start, end))
    notices = [lh.normalize_notice(r) for r in raw_notices]
    notices_const = [n for n in notices if n.get("work_type") == "공사"]
    notices_ok = [n for n in notices_const if lh.passes_filter(n)]
    notices_unpriced = [n for n in notices_const if n.get("estimated_price") is None]
    logger.info("  공고 전체=%d 공사=%d 100억이상=%d 미공개=%d",
                len(notices), len(notices_const), len(notices_ok), len(notices_unpriced))
    upsert_notices(conn, notices_ok)

    # 개찰 수집
    logger.info("[2/3] 개찰결과 수집 중...")
    raw_awards = list(lh.fetch_awards(start, end))
    awards = [lh.normalize_award(r) for r in raw_awards]
    insert_awards(conn, awards)
    logger.info("  개찰 %d건 적재", len(awards))

    # 계약 수집
    logger.info("[3/3] 계약현황 수집 중...")
    raw_contracts = list(lh.fetch_contracts(start, end))
    contracts = [lh.normalize_contract(r) for r in raw_contracts]
    insert_contracts(conn, contracts)
    logger.info("  계약 %d건 적재", len(contracts))

    # 조인 CSV
    joined = join_all(notices_ok, awards, contracts)
    csv_path = Path(output_dir) / f"lh_joined_{month.replace('-', '')}.csv"
    joined.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info("CSV 저장: %s (%d행)", csv_path, len(joined))

    # source_runs 기록
    ended_at = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO source_runs
          (run_id, source, started_at, ended_at, status, fetched_count, filtered_count)
        VALUES (?,?,?,?,?,?,?)
    """, (run_id, "lh", started_at, ended_at, "success",
          len(raw_notices) + len(raw_awards) + len(raw_contracts), len(notices_ok)))
    conn.commit()
    conn.close()
    logger.info("=== 완료 ===")
    return str(csv_path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="YYYY-MM (예: 2026-05)")
    ap.add_argument("--db", default="procurement.db")
    ap.add_argument("--output", default="output")
    args = ap.parse_args()
    run(args.month, args.db, args.output)
