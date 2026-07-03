"""run_monthly.py — 월간 수집 (LH·G2B·KR Rail) + notice_no 조인 + CSV 저장

사용법:
    python -m src.run_monthly --month 2026-05
    python -m src.run_monthly --month 2026-05 --db procurement.db
    python -m src.run_monthly --month 2026-05 --sources lh,kr_rail
"""
import uuid
import logging
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from dateutil.relativedelta import relativedelta
import pandas as pd

from src.adapters.lh import LHAdapter
from src.adapters.g2b_opnstd import G2BOpnStdAdapter
from src.adapters.kr_rail import KRRailAdapter
from src.db import (
    get_connection,
    ensure_schema,
    upsert_notices,
    insert_unpriced_notices,
    insert_awards,
    insert_contracts,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_monthly")

# 어댑터별 API 키 환경변수는 각 어댑터가 생성 시점에 직접 읽는다.
# KEPCO는 자체 어댑터가 아직 없어 Phase 1 목록에서 제외되어 있다 (README 참고).
SOURCES: Dict[str, Callable] = {
    "lh":        lambda: LHAdapter(),
    "g2b_opnstd": lambda: G2BOpnStdAdapter(),
    "kr_rail":   lambda: KRRailAdapter(),
}

NOTICE_COLS = [
    "notice_no", "title", "construction_type", "bid_method",
    "is_long_term_continuing", "estimated_price", "posted_at", "status",
    "zone_hq", "license_conditions", "vendor_restrictions",
]
AWARD_COLS = ["notice_no", "bidder_name", "bidder_biz_no", "award_price", "award_rate", "expect_price"]
CONTRACT_COLS = ["notice_no", "contract_name", "contract_price", "contracted_at",
                  "contractor_name", "start_date", "end_date"]


# ── 기간 계산 ──────────────────────────────────────────────────────────────

def month_bounds(ym: str) -> tuple[date, date]:
    y, m = map(int, ym.split("-"))
    start = date(y, m, 1)
    end = (start + relativedelta(months=1)) - timedelta(days=1)
    return start, end


# ── 어댑터 형태 흡수 (LH: notice/award/contract 3분리, G2B계열: 단일 normalize) ──

def _collect_notices(adapter, start: date, until: date) -> Tuple[list, list]:
    raw = list(adapter.fetch_notices(start, until))
    if hasattr(adapter, "normalize_notice"):
        notices = [adapter.normalize_notice(r) for r in raw]
    else:
        # G2B계열 normalize()는 낙찰/계약 임시필드(_award_*, _contract_*)를
        # 같은 dict에 함께 담아 반환한다. notices 테이블에는 불필요하므로 제거.
        notices = [{k: v for k, v in adapter.normalize(r).items() if not k.startswith("_")} for r in raw]
    return raw, notices


def _collect_awards(adapter, start: date, until: date) -> Tuple[list, list]:
    raw = list(adapter.fetch_awards(start, until))
    if hasattr(adapter, "normalize_award"):
        awards = [adapter.normalize_award(r) for r in raw]
    else:
        awards = []
        for r in raw:
            n = adapter.normalize(r)
            awards.append({
                "source":        n.get("source"),
                "notice_no":     n.get("notice_no"),
                "bidder_name":   n.get("_award_corp"),
                "bidder_biz_no": n.get("_award_corp_bizrno"),
                "award_price":   n.get("_award_amt"),
                "award_rate":    n.get("_award_rate"),
                "winner_status": "낙찰" if n.get("_award_corp") else None,
                "raw_payload":   r,
            })
    return raw, awards


def _collect_contracts(adapter, start: date, until: date) -> Tuple[list, list]:
    raw = list(adapter.fetch_contracts(start, until))
    if hasattr(adapter, "normalize_contract"):
        contracts = [adapter.normalize_contract(r) for r in raw]
    else:
        contracts = []
        for r in raw:
            n = adapter.normalize(r)
            contracts.append({
                "source":         n.get("source"),
                "notice_no":      n.get("notice_no"),
                "contract_name":  n.get("title"),
                "contract_price": n.get("_contract_amt"),
                "contracted_at":  n.get("_contract_date"),
                "raw_payload":    r,
            })
    return raw, contracts


# ── 조인 ──────────────────────────────────────────────────────────────────

def _select(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols]


def join_all(source: str, notices: list[dict], awards: list[dict], contracts: list[dict]) -> pd.DataFrame:
    ndf = pd.DataFrame(notices)
    ndf = _select(ndf, NOTICE_COLS).drop_duplicates("notice_no") if not ndf.empty else pd.DataFrame(columns=NOTICE_COLS)

    adf = pd.DataFrame(awards)
    if not adf.empty:
        if "winner_status" in adf.columns:
            winners = adf[adf["winner_status"].astype(str).str.contains("낙찰", na=False)]
            adf = winners if not winners.empty else adf
        adf = _select(adf, AWARD_COLS).rename(columns={
            "bidder_name": "winner_name", "bidder_biz_no": "winner_biz_no",
        }).drop_duplicates("notice_no")
    else:
        adf = pd.DataFrame(columns=["notice_no", "winner_name", "winner_biz_no", "award_price", "award_rate", "expect_price"])

    cdf = pd.DataFrame(contracts)
    cdf = _select(cdf, CONTRACT_COLS).drop_duplicates("notice_no") if not cdf.empty else pd.DataFrame(columns=CONTRACT_COLS)

    merged = ndf.merge(adf, on="notice_no", how="left").merge(cdf, on="notice_no", how="left")
    merged.insert(0, "source", source)
    return merged


# ── 메인 ──────────────────────────────────────────────────────────────────

def run(month: str, db_path: str = "procurement.db", output_dir: str = "output",
        sources: Optional[List[str]] = None) -> str:
    active = sources or list(SOURCES.keys())
    unknown = [s for s in active if s not in SOURCES]
    if unknown:
        raise ValueError(f"알 수 없는 source: {unknown} (사용가능: {list(SOURCES.keys())})")

    start, end = month_bounds(month)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    ensure_schema(conn)

    all_joined = []
    any_success = False

    for source in active:
        run_id = str(uuid.uuid4())[:8]
        started_at = datetime.now().isoformat()
        logger.info("=== [%s] 수집 시작 | %s ~ %s | run_id=%s ===", source, start, end, run_id)
        try:
            adapter = SOURCES[source]()

            raw_notices, notices = _collect_notices(adapter, start, end)
            notices_const = [n for n in notices if n.get("work_type") == "공사"]
            notices_ok = [n for n in notices_const if adapter.passes_filter(n)]
            notices_unpriced = [n for n in notices_const if n.get("estimated_price") is None]
            logger.info("  [%s] 공고 전체=%d 공사=%d 100억이상=%d 미공개=%d",
                        source, len(notices), len(notices_const), len(notices_ok), len(notices_unpriced))
            upsert_notices(conn, notices_ok)
            insert_unpriced_notices(conn, notices_unpriced)

            # 개방표준 API는 서버측 기관/공사 필터가 없어 전국 낙찰·계약이 수집된다.
            # 우리가 필요한 건 필터된 공고(공사 100억↑)에 매칭되는 건뿐이므로,
            # 대상 공고번호 집합으로 좁혀서 DB/CSV에 전국 데이터가 쌓이지 않게 한다.
            target_nos = {n.get("notice_no") for n in notices_ok if n.get("notice_no")}

            raw_awards, awards = _collect_awards(adapter, start, end)
            awards = [a for a in awards if a.get("notice_no") in target_nos]
            insert_awards(conn, awards)
            logger.info("  [%s] 개찰/낙찰 %d건 적재 (전국 수집 %d건 중 대상 매칭)",
                        source, len(awards), len(raw_awards))

            raw_contracts, contracts = _collect_contracts(adapter, start, end)
            contracts = [c for c in contracts if c.get("notice_no") in target_nos]
            insert_contracts(conn, contracts)
            logger.info("  [%s] 계약 %d건 적재 (전국 수집 %d건 중 대상 매칭)",
                        source, len(contracts), len(raw_contracts))

            joined = join_all(source, notices_ok, awards, contracts)
            csv_path = Path(output_dir) / f"{source}_joined_{month.replace('-', '')}.csv"
            joined.to_csv(csv_path, index=False, encoding="utf-8-sig")
            logger.info("  [%s] CSV 저장: %s (%d행)", source, csv_path, len(joined))
            all_joined.append(joined)

            ended_at = datetime.now().isoformat()
            conn.execute("""
                INSERT OR REPLACE INTO source_runs
                  (run_id, source, started_at, ended_at, status, fetched_count, filtered_count, error_message)
                VALUES (?,?,?,?,?,?,?,?)
            """, (run_id, source, started_at, ended_at, "success",
                  len(raw_notices) + len(raw_awards) + len(raw_contracts), len(notices_ok), None))
            conn.commit()
            any_success = True
        except Exception as e:
            logger.exception("[%s] 수집 실패", source)
            ended_at = datetime.now().isoformat()
            conn.execute("""
                INSERT OR REPLACE INTO source_runs
                  (run_id, source, started_at, ended_at, status, fetched_count, filtered_count, error_message)
                VALUES (?,?,?,?,?,?,?,?)
            """, (run_id, source, started_at, ended_at, "error", 0, 0, str(e)))
            conn.commit()

    if all_joined:
        combined = pd.concat(all_joined, ignore_index=True)
        combined_path = Path(output_dir) / f"all_joined_{month.replace('-', '')}.csv"
        combined.to_csv(combined_path, index=False, encoding="utf-8-sig")
        total_price = int(combined["estimated_price"].fillna(0).sum())
        logger.info("전체 통합 CSV 저장: %s (%d행, 추정가격 합계=%s원)",
                    combined_path, len(combined), format(total_price, ","))

    conn.close()
    if not any_success:
        raise RuntimeError("모든 소스 수집 실패 — 위 로그의 개별 에러 확인 필요")
    logger.info("=== 완료 ===")
    return str(Path(output_dir) / f"all_joined_{month.replace('-', '')}.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="YYYY-MM (예: 2026-05)")
    ap.add_argument("--db", default="procurement.db")
    ap.add_argument("--output", default="output")
    ap.add_argument("--sources", default=None, help="쉼표구분 source 목록 (예: lh,kr_rail). 미지정시 전체")
    args = ap.parse_args()
    source_list = args.sources.split(",") if args.sources else None
    run(args.month, args.db, args.output, source_list)
