"""run_monthly.py — 월간 수집 (LH·G2B·KR Rail·KEPCO) + notice_no 조인 + CSV 저장

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

from src.adapters.base import CONSTRUCTION_MIN_PRICE
from src.adapters.lh import LHAdapter
from src.adapters.g2b_opnstd import G2BOpnStdAdapter
from src.adapters.kr_rail import KRRailAdapter
from src.adapters.kepco import KEPCOAdapter
from src.adapters.kwater import KWaterAdapter
from src.adapters.ex import EXAdapter
from src.adapters.kogas import KOGASAdapter
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
SOURCES: Dict[str, Callable] = {
    "lh":        lambda: LHAdapter(),        # LH_API_KEY
    "g2b_opnstd": lambda: G2BOpnStdAdapter(),  # G2B_API_KEY
    "kr_rail":   lambda: KRRailAdapter(),    # G2B_API_KEY 공유
    "kepco":     lambda: KEPCOAdapter(),     # KEPCO_API_KEY
    "kwater":    lambda: KWaterAdapter(),    # 키 불필요 (비로그인 XHR)
    "ex":        lambda: EXAdapter(),        # EX_API_KEY (data.ex.co.kr, 계약 중심)
    "kogas":     lambda: KOGASAdapter(),     # 키 불필요 (비로그인 스크래핑)
}

# 자체 시스템으로 기관 범위가 이미 한정된 소스 (fetch→process를 소스별 독립 실행)
SELF_SCOPED = ["lh", "kepco", "kwater", "ex", "kogas"]

NOTICE_COLS = [
    "notice_no", "title", "construction_type", "bid_method",
    "is_long_term_continuing", "estimated_price", "posted_at", "status",
    "zone_hq", "license_conditions", "vendor_restrictions",
]
AWARD_COLS = ["notice_no", "bidder_name", "bidder_biz_no", "award_price", "award_rate", "expect_price"]
CONTRACT_COLS = ["notice_no", "contract_name", "contract_price", "contracted_at",
                  "contractor_name", "start_date", "end_date"]

# 체결일 기준 100억↑ 공사계약 CSV 출력 컬럼 (목표 산출물).
CONTRACT_OUT_COLS = [
    "source", "contracted_at", "demand_inst", "contract_name", "bsns_div",
    "contract_price", "total_contract_price", "is_long_term", "contract_method",
    "contractor_name", "contractor_bizno", "contract_no", "notice_no",
]


def _is_target_contract(c: dict) -> bool:
    """체결일 기준 대상 계약: 계약금액 100억↑ + 공사(구분 정보가 있으면 공사)."""
    price = c.get("contract_price")
    if price is None or price < CONSTRUCTION_MIN_PRICE:
        return False
    bd = c.get("bsns_div")
    return bd is None or "공사" in str(bd)


# ── 기간 계산 ──────────────────────────────────────────────────────────────

def month_bounds(ym: str) -> tuple[date, date]:
    y, m = map(int, ym.split("-"))
    start = date(y, m, 1)
    end = (start + relativedelta(months=1)) - timedelta(days=1)
    return start, end


# ── 어댑터 형태 흡수 (LH: notice/award/contract 3분리, G2B계열: 단일 normalize) ──
#
# 가져오기(fetch)와 정규화(normalize)를 분리한다. G2B 계열(g2b_opnstd·kr_rail)은
# 같은 나라장터 개방표준 API를 쓰므로, run()에서 전국 원본을 1회만 fetch하고
# 각 소스가 그 원본을 정규화·필터링하도록 하여 중복 전국 순회를 없앤다.

def _normalize_notices(adapter, raw: list) -> list:
    if hasattr(adapter, "normalize_notice"):
        return [adapter.normalize_notice(r) for r in raw]
    # G2B계열 normalize()는 낙찰/계약 임시필드(_award_*, _contract_*)를
    # 같은 dict에 함께 담아 반환한다. notices 테이블에는 불필요하므로 제거.
    return [{k: v for k, v in adapter.normalize(r).items() if not k.startswith("_")} for r in raw]


def _normalize_awards(adapter, raw: list) -> list:
    if hasattr(adapter, "normalize_award"):
        return [adapter.normalize_award(r) for r in raw]
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
    return awards


def _normalize_contracts(adapter, raw: list) -> list:
    if hasattr(adapter, "normalize_contract"):
        return [adapter.normalize_contract(r) for r in raw]
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
    return contracts


def _fetch_all(adapter, start: date, until: date) -> Tuple[list, list, list]:
    """어댑터에서 공고·낙찰·계약 원본을 각각 materialize."""
    return (
        list(adapter.fetch_notices(start, until)),
        list(adapter.fetch_awards(start, until)),
        list(adapter.fetch_contracts(start, until)),
    )


def _log_schema(kind: str, rows: list) -> None:
    """원본 레코드의 실제 필드명을 1회 로그로 남긴다(스키마 파악·모델 설계용)."""
    if rows:
        logger.info("[schema] %s 레코드 %d건, 필드=%s",
                    kind, len(rows), sorted(rows[0].keys()))


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

def _process_source(conn, source, adapter, raw_notices, raw_awards, raw_contracts,
                    label, output_dir, all_joined) -> bool:
    """이미 fetch된 원본을 정규화·필터링·적재하고 조인 CSV를 만든다. 성공 여부 반환."""
    run_id = str(uuid.uuid4())[:8]
    started_at = datetime.now().isoformat()
    try:
        notices = _normalize_notices(adapter, raw_notices)
        notices_const = [n for n in notices if n.get("work_type") == "공사"]
        notices_ok = [n for n in notices_const if adapter.passes_filter(n)]
        notices_unpriced = [n for n in notices_const if n.get("estimated_price") is None]
        logger.info("  [%s] 공고 전체=%d 공사=%d 100억이상=%d 미공개=%d",
                    source, len(notices), len(notices_const), len(notices_ok), len(notices_unpriced))
        upsert_notices(conn, notices_ok)
        insert_unpriced_notices(conn, notices_unpriced)

        # 개방표준 API는 서버측 기관/공사 필터가 없어 전국 낙찰·계약이 수집된다.
        # 필요한 건 필터된 공고(공사 100억↑)에 매칭되는 건뿐이므로, 대상 공고번호
        # 집합으로 좁혀서 DB/CSV에 전국 데이터가 쌓이지 않게 한다.
        target_nos = {n.get("notice_no") for n in notices_ok if n.get("notice_no")}

        awards = [a for a in _normalize_awards(adapter, raw_awards)
                  if a.get("notice_no") in target_nos]
        insert_awards(conn, awards)
        logger.info("  [%s] 개찰/낙찰 %d건 적재 (수집 %d건 중 대상 매칭)",
                    source, len(awards), len(raw_awards))

        # 계약: 체결일(cntrctCnclsDate) 기준 독립 수집. 공고 매칭이 아니라
        # 공사 + 계약금액 100억↑ 조건으로 직접 집계 → 공사이행보증서 대상 규모.
        if hasattr(adapter, "is_large_construction_contract"):
            raw_c_target = [r for r in raw_contracts if adapter.is_large_construction_contract(r)]
        else:
            raw_c_target = raw_contracts  # LH 등: 정규화 후 가격 기준으로 필터
        contracts = [c for c in _normalize_contracts(adapter, raw_c_target)
                     if _is_target_contract(c)]
        insert_contracts(conn, contracts)
        logger.info("  [%s] 100억↑ 공사계약 %d건 적재 (전국 수집 %d건 중)",
                    source, len(contracts), len(raw_contracts))

        # 계약 CSV (체결일 기준 100억↑ 공사계약 = 핵심 산출물)
        if contracts:
            cdf = pd.DataFrame(contracts)
            cols = [c for c in CONTRACT_OUT_COLS if c in cdf.columns]
            cpath = Path(output_dir) / f"{source}_contracts_{label}.csv"
            cdf[cols].sort_values("contract_price", ascending=False).to_csv(
                cpath, index=False, encoding="utf-8-sig")
            logger.info("  [%s] 계약 CSV 저장: %s", source, cpath)

        joined = join_all(source, notices_ok, awards, contracts)
        csv_path = Path(output_dir) / f"{source}_joined_{label}.csv"
        joined.to_csv(csv_path, index=False, encoding="utf-8-sig")
        logger.info("  [%s] 공고기준 조인 CSV 저장: %s (%d행)", source, csv_path, len(joined))
        all_joined.append(joined)

        ended_at = datetime.now().isoformat()
        conn.execute("""
            INSERT OR REPLACE INTO source_runs
              (run_id, source, started_at, ended_at, status, fetched_count, filtered_count, error_message)
            VALUES (?,?,?,?,?,?,?,?)
        """, (run_id, source, started_at, ended_at, "success",
              len(raw_notices) + len(raw_awards) + len(raw_contracts), len(notices_ok), None))
        conn.commit()
        return True
    except Exception as e:
        logger.exception("[%s] 처리 실패", source)
        ended_at = datetime.now().isoformat()
        conn.execute("""
            INSERT OR REPLACE INTO source_runs
              (run_id, source, started_at, ended_at, status, fetched_count, filtered_count, error_message)
            VALUES (?,?,?,?,?,?,?,?)
        """, (run_id, source, started_at, ended_at, "error", 0, 0, str(e)))
        conn.commit()
        return False


def _record_fetch_error(conn, source, started_at, err) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO source_runs
          (run_id, source, started_at, ended_at, status, fetched_count, filtered_count, error_message)
        VALUES (?,?,?,?,?,?,?,?)
    """, (str(uuid.uuid4())[:8], source, started_at, datetime.now().isoformat(),
          "error", 0, 0, str(err)))
    conn.commit()


def run(month: Optional[str] = None, db_path: str = "procurement.db", output_dir: str = "output",
        sources: Optional[List[str]] = None,
        since: Optional[str] = None, until: Optional[str] = None) -> str:
    active = sources or list(SOURCES.keys())
    unknown = [s for s in active if s not in SOURCES]
    if unknown:
        raise ValueError(f"알 수 없는 source: {unknown} (사용가능: {list(SOURCES.keys())})")

    # 기간: --since/--until(임의 구간, 짧은 스모크 테스트용)이 우선, 없으면 --month.
    if since and until:
        start = datetime.strptime(since, "%Y-%m-%d").date()
        end = datetime.strptime(until, "%Y-%m-%d").date()
        if start > end:
            raise ValueError(f"since({since}) > until({until})")
        label = f"{start:%Y%m%d}_{end:%Y%m%d}"
    elif month:
        start, end = month_bounds(month)
        label = month.replace("-", "")
    else:
        raise ValueError("--month 또는 --since/--until 중 하나는 필요합니다.")
    logger.info("수집 기간: %s ~ %s (label=%s)", start, end, label)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    ensure_schema(conn)

    all_joined = []
    any_success = False

    # ── 자체 OpenAPI 소스 (LH·KEPCO: 기관 범위가 이미 한정됨) ────────────────
    for source in [s for s in SELF_SCOPED if s in active]:
        logger.info("=== [%s] 수집 시작 | %s ~ %s ===", source, start, end)
        started_at = datetime.now().isoformat()
        try:
            adapter = SOURCES[source]()
        except ValueError as e:
            # API 키 미설정 소스는 경고 후 skip (부분 운영 허용, 실행계획 §2.3)
            logger.warning("[%s] 어댑터 생성 실패 — skip: %s", source, e)
            continue
        try:
            rn, ra, rc = _fetch_all(adapter, start, end)
            _log_schema(f"{source} 공고", rn)
            _log_schema(f"{source} 개찰", ra)
            _log_schema(f"{source} 계약", rc)
            if _process_source(conn, source, adapter, rn, ra, rc, label, output_dir, all_joined):
                any_success = True
        except Exception as e:
            logger.exception("[%s] 수집 실패", source)
            _record_fetch_error(conn, source, started_at, e)

    # ── G2B 계열: 나라장터 개방표준 API를 1회만 fetch하여 공유 ──────────────
    #    g2b_opnstd = 전국 전체, kr_rail = 그중 국가철도공단분.
    #    (예전엔 소스마다 전국을 각각 재수집해 비용이 2배였다.)
    g2b_family = [s for s in active if s in ("g2b_opnstd", "kr_rail")]
    if g2b_family:
        logger.info("=== [G2B 공용] 전국 수집 시작 | %s ~ %s | 대상 소스=%s ===",
                    start, end, ",".join(g2b_family))
        started_at = datetime.now().isoformat()
        try:
            g2b = SOURCES["g2b_opnstd"]()
            # 1) 공고는 전국 1회 수집. 2) 그중 공사 100억↑ 공고번호(target_nos)를
            #    구해, 3) 계약·낙찰은 그 공고번호로만 스코프 조회한다(전국 순회 회피).
            raw_n = list(g2b.fetch_notices(start, end))
            g2b_norm = _normalize_notices(g2b, raw_n)
            target_nos = {n.get("notice_no") for n in g2b_norm
                          if n.get("work_type") == "공사"
                          and g2b.passes_filter(n) and n.get("notice_no")}
            logger.info("[G2B 공용] 전국 공고=%d, 대상(공사100억↑)=%d → 계약·낙찰은 대상 공고번호로만 조회",
                        len(raw_n), len(target_nos))
            raw_a = list(g2b.fetch_awards_scoped(target_nos, start, end))
            raw_c = list(g2b.fetch_contracts_scoped(target_nos, start, end))
            logger.info("[G2B 공용] 수집 완료: 공고=%d 낙찰=%d 계약=%d",
                        len(raw_n), len(raw_a), len(raw_c))
            _log_schema("G2B 공고", raw_n)
            _log_schema("G2B 낙찰", raw_a)
            _log_schema("G2B 계약", raw_c)
        except Exception as e:
            logger.exception("[G2B 공용] 전국 수집 실패 — g2b_opnstd·kr_rail 모두 스킵")
            for source in g2b_family:
                _record_fetch_error(conn, source, started_at, e)
            raw_n = None

        if raw_n is not None:
            for source in g2b_family:
                if source == "kr_rail":
                    kr = SOURCES["kr_rail"]()
                    fn = [r for r in raw_n if kr._is_kr_rail(r)]
                    fa = [r for r in raw_a if kr._is_kr_rail(r)]
                    fc = [r for r in raw_c if kr._is_kr_rail(r)]
                    logger.info("=== [kr_rail] 국가철도공단 필터 | 공고=%d 낙찰=%d 계약=%d ===",
                                len(fn), len(fa), len(fc))
                    if _process_source(conn, "kr_rail", kr, fn, fa, fc, label, output_dir, all_joined):
                        any_success = True
                else:
                    logger.info("=== [g2b_opnstd] 전국 처리 ===")
                    if _process_source(conn, "g2b_opnstd", g2b, raw_n, raw_a, raw_c,
                                       label, output_dir, all_joined):
                        any_success = True

    if all_joined:
        combined = pd.concat(all_joined, ignore_index=True)
        combined_path = Path(output_dir) / f"all_joined_{label}.csv"
        combined.to_csv(combined_path, index=False, encoding="utf-8-sig")
        total_price = int(combined["estimated_price"].fillna(0).sum())
        logger.info("전체 통합 CSV 저장: %s (%d행, 추정가격 합계=%s원)",
                    combined_path, len(combined), format(total_price, ","))

    conn.close()
    if not any_success:
        raise RuntimeError("모든 소스 수집 실패 — 위 로그의 개별 에러 확인 필요")
    logger.info("=== 완료 ===")
    return str(Path(output_dir) / f"all_joined_{label}.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default=None, help="YYYY-MM (예: 2026-05). --since/--until 없을 때 사용")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD 임의 시작일 (짧은 스모크 테스트용)")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD 임의 종료일")
    ap.add_argument("--db", default="procurement.db")
    ap.add_argument("--output", default="output")
    ap.add_argument("--sources", default=None, help="쉼표구분 source 목록 (예: lh,kr_rail). 미지정시 전체")
    args = ap.parse_args()
    source_list = args.sources.split(",") if args.sources else None
    run(args.month, args.db, args.output, source_list, since=args.since, until=args.until)
