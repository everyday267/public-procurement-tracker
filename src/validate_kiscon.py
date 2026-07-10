"""validate_kiscon.py — 수집 데이터 ↔ KISCON 대조 검증 (검증 층 2).

사용법:
    python -m src.validate_kiscon --db procurement.db --month 2026-06
    python -m src.validate_kiscon --db procurement.db --skip-fetch   # 수집 생략, 대조만
    python -m src.validate_kiscon --db procurement.db --full-backfill

설계 (docs 설계문서 §5):
  KISCON은 정답지가 아니라 "두 번째 관측치"다. 우리 DB는 100억↑ 공사만 담으므로
  KISCON 공공×원도급 총액의 부분집합이어야 한다 (ratio < 1 불변식).

  L0_AMT  : 월별 계약금액 합계 대조 (StatAmt). ratio = 우리 ÷ KISCON.
            basis=contract_month(단순 월대월) / lag_adjusted(통보 30일 지연 보정:
            우리 m월 vs KISCON m~m+1월).
  L0_CNT  : 월별 건수 대조 (StatCnt). 참고 지표 (100억↑ 건수 비중은 원래 작다).
  L2      : 건별 매칭 (kiscon_records 확보 시) → Lincoln-Petersen 모집단 추정.

비교가능 모집단(comparable universe) 정렬 — 대조 전 우리 DB에서:
  - kr_rail 제외 (g2b_opnstd 부분집합 재수집 → 이중계상)
  - (contract_no, contracted_at, contract_price) 중복 제거
  - 공사만 (bsns_div)
  - 변경계약 제외 (contract_status 키워드)
  - 전기공사·정보통신·소방 제외 (KISCON은 건산법 종합·전문만) — 키워드 휴리스틱
"""
import argparse
import logging
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dateutil.relativedelta import relativedelta

from .db import get_connection, ensure_schema, upsert_kiscon_recon

logger = logging.getLogger("validate_kiscon")

# ratio 정상 밴드 (첫 실측 후 캘리브레이션 예정 — 의도적으로 넓게 시작)
RATIO_BAND = (0.15, 0.90)
# 월간 ratio 급변 임계
RATIO_JUMP_THRESHOLD = 0.20
# 통보 지연 허용 일수 (계약 후 30일 이내 통보 + 여유 1주)
NOTI_LAG_DAYS = 37
# 100억 (계약금액 필터, base.CONSTRUCTION_MIN_PRICE와 동일 값)
MIN_PRICE = 10_000_000_000
# L2 2차 매칭 허용 오차·유사도
PRICE_TOLERANCE = 0.005
NAME_SIMILARITY_MIN = 0.75

# 비교가능 모집단 SQL. strict=True면 KISCON 대상외 업종(전기·정보통신·소방)까지 제외.
_UNIVERSE_SQL = """
SELECT DISTINCT contract_no, contracted_at, contract_price, contract_name
FROM contracts
WHERE source != 'kr_rail'
  AND (bsns_div IS NULL OR bsns_div LIKE '%공사%')
  AND contract_price IS NOT NULL
  AND contracted_at IS NOT NULL AND contracted_at != ''
  AND IFNULL(contract_status, '') NOT LIKE '%변경%'
"""
_STRICT_EXCLUDE = """
  AND IFNULL(contract_name, '') NOT LIKE '%전기공사%'
  AND IFNULL(contract_name, '') NOT LIKE '%정보통신%'
  AND IFNULL(contract_name, '') NOT LIKE '%소방%'
"""


# ---------------------------------------------------------------------- #
# 집계                                                                     #
# ---------------------------------------------------------------------- #

def our_monthly_totals(conn, strict=True):
    # type: (object, bool) -> Dict[str, dict]
    """우리 DB 비교가능 모집단의 월별 합계. {ym: {krw, n}}"""
    sql = _UNIVERSE_SQL + (_STRICT_EXCLUDE if strict else "")
    cur = conn.execute(
        "SELECT substr(contracted_at,1,7) AS ym, SUM(contract_price) AS krw, COUNT(*) AS n "
        "FROM ({}) GROUP BY ym".format(sql)
    )
    return {r["ym"]: {"krw": int(r["krw"]), "n": int(r["n"])} for r in cur.fetchall()}


def our_universe_rows(conn, months, strict=True):
    # type: (object, List[str], bool) -> List[dict]
    """L2 매칭용 건별 목록 (대상 월 한정)."""
    sql = _UNIVERSE_SQL + (_STRICT_EXCLUDE if strict else "")
    cur = conn.execute(
        "SELECT * FROM ({}) WHERE substr(contracted_at,1,7) IN ({})".format(
            sql, ",".join("?" * len(months))),
        months,
    )
    return [dict(r) for r in cur.fetchall()]


def kiscon_monthly_totals(conn):
    # type: (object) -> Dict[str, dict]
    """KISCON 공공×원도급 월합계. {ym: {krw, cnt}} (amt는 억원 → 원 환산)"""
    cur = conn.execute("""
        SELECT substr(noti_date,1,4) || '-' || substr(noti_date,5,2) AS ym,
               SUM(amt_100m) AS amt_100m, SUM(cnt) AS cnt
        FROM kiscon_stats
        WHERE balju_code = '0' AND dogub_code = '1'
        GROUP BY ym
    """)
    out = {}
    for r in cur.fetchall():
        amt = r["amt_100m"]
        out[r["ym"]] = {
            "krw": int(amt * 100_000_000) if amt is not None else None,
            "cnt": int(r["cnt"]) if r["cnt"] is not None else None,
        }
    return out


def _next_ym(ym):
    # type: (str) -> str
    d = datetime.strptime(ym, "%Y-%m").date() + relativedelta(months=1)
    return d.strftime("%Y-%m")


# ---------------------------------------------------------------------- #
# L0 대조                                                                  #
# ---------------------------------------------------------------------- #

def reconcile_l0(ours, kiscon, months):
    # type: (Dict[str, dict], Dict[str, dict], List[str]) -> List[dict]
    """월별 L0_AMT/L0_CNT recon 행 생성. 플래그는 L0_AMT×lag_adjusted 기준."""
    rows = []
    lag_ratios = {}  # ym -> ratio (RATIO_JUMP 계산용)

    for ym in sorted(months):
        o = ours.get(ym, {"krw": 0, "n": 0})
        k_now = kiscon.get(ym) or {}
        k_next = kiscon.get(_next_ym(ym)) or {}

        for basis in ("contract_month", "lag_adjusted"):
            if basis == "contract_month":
                k_krw, k_cnt = k_now.get("krw"), k_now.get("cnt")
            else:
                parts = [v for v in (k_now.get("krw"), k_next.get("krw")) if v is not None]
                k_krw = sum(parts) if parts else None
                cparts = [v for v in (k_now.get("cnt"), k_next.get("cnt")) if v is not None]
                k_cnt = sum(cparts) if cparts else None

            ratio = (o["krw"] / k_krw) if (k_krw and o["krw"] is not None) else None
            flag = None
            if basis == "lag_adjusted":
                if k_krw is None:
                    flag = "NO_KISCON_DATA"
                elif ratio is not None and ratio >= 1.0:
                    flag = "RATIO_GE_1"
                elif ratio is not None and not (RATIO_BAND[0] <= ratio <= RATIO_BAND[1]):
                    flag = "OUT_OF_BAND"
                if ratio is not None:
                    lag_ratios[ym] = (ratio, len(rows))  # 행 인덱스 기억 (JUMP 후처리)

            rows.append({
                "ym": ym, "level": "L0_AMT", "basis": basis,
                "ours_krw": o["krw"], "kiscon_krw": k_krw, "ratio": ratio,
                "n_ours": o["n"], "n_kiscon": k_cnt,
                "n_matched": None, "n_hat": None,
                "flag": flag, "detail": None,
            })
            if k_cnt is not None or basis == "contract_month":
                rows.append({
                    "ym": ym, "level": "L0_CNT", "basis": basis,
                    "ours_krw": None, "kiscon_krw": None,
                    "ratio": (o["n"] / k_cnt) if k_cnt else None,
                    "n_ours": o["n"], "n_kiscon": k_cnt,
                    "n_matched": None, "n_hat": None,
                    "flag": None, "detail": None,
                })

    # RATIO_JUMP: 인접 월 lag_adjusted ratio 급변 (기존 플래그 없을 때만)
    yms = sorted(lag_ratios)
    for prev, cur in zip(yms, yms[1:]):
        (r_prev, _), (r_cur, idx) = lag_ratios[prev], lag_ratios[cur]
        if abs(r_cur - r_prev) > RATIO_JUMP_THRESHOLD and rows[idx]["flag"] is None:
            rows[idx]["flag"] = "RATIO_JUMP"
            rows[idx]["detail"] = "전월 ratio={:.3f} → {:.3f}".format(r_prev, r_cur)

    return rows


# ---------------------------------------------------------------------- #
# L2 건별 매칭                                                              #
# ---------------------------------------------------------------------- #

_CORP_NOISE = re.compile(r"㈜|\(주\)|주식회사|\s+")
_PHASE_TOKEN = re.compile(r"제?\d+(차|단계|공구|공区|블록)")
_SPLIT = re.compile(r"[\s\(\)\[\]·,/·-]+")


def normalize_name(name):
    # type: (Optional[str]) -> str
    return _CORP_NOISE.sub("", name or "")


def work_tokens(name):
    # type: (Optional[str]) -> Tuple[set, set]
    """공사명 → (본문 토큰, 공구/차수 토큰). 차수는 장기계속 식별키로 분리 보관."""
    tokens = {t for t in _SPLIT.split(name or "") if t}
    phase = {t for t in tokens if _PHASE_TOKEN.fullmatch(t)}
    return tokens - phase, phase


def name_similarity(a, b):
    # type: (Optional[str], Optional[str]) -> float
    """토큰 집합 코사인 유사도."""
    ta, _ = work_tokens(a)
    tb, _ = work_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / math.sqrt(len(ta) * len(tb))


def _parse_date(value):
    # type: (Optional[str]) -> Optional[date]
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))[:8]
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None


def kiscon_record_rows(conn, months):
    # type: (object, List[str]) -> List[dict]
    """L2 대상 KISCON 건별 레코드: 공공×원도급, 100억↑, 통보일이 대상월~+37일."""
    if not months:
        return []
    start = datetime.strptime(min(months), "%Y-%m").date()
    end = (datetime.strptime(max(months), "%Y-%m").date()
           + relativedelta(months=1) + timedelta(days=NOTI_LAG_DAYS))
    cur = conn.execute(
        "SELECT * FROM kiscon_records "
        "WHERE balju_code='0' AND dogub_code='1' AND contract_price >= ? "
        "AND noti_date >= ? AND noti_date <= ?",
        (MIN_PRICE, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")),
    )
    return [dict(r) for r in cur.fetchall()]


def match_records(ours, kiscon_records):
    # type: (List[dict], List[dict]) -> Tuple[List[tuple], List[dict], List[dict]]
    """설계문서 §5.4 매칭 캐스케이드.

    1차: 계약금액 완전일치 & 통보일이 계약일 0~+37일  → 확정매칭
    2차: 금액 오차 ≤0.5% & 공사명 유사도 ≥0.75 & 날짜 조건 → 확률매칭
    반환: (매칭쌍 목록, 미매칭 우리건, 미매칭 KISCON건)
    """
    used = set()
    matches = []

    def candidates(contract, exact):
        c_date = _parse_date(contract.get("contracted_at"))
        price = contract["contract_price"]
        for i, rec in enumerate(kiscon_records):
            if i in used or rec.get("contract_price") is None:
                continue
            n_date = _parse_date(rec.get("noti_date"))
            if c_date and n_date and not (timedelta(0) <= n_date - c_date
                                          <= timedelta(days=NOTI_LAG_DAYS)):
                continue
            if exact:
                if rec["contract_price"] == price:
                    yield i, 1.0
            else:
                if price and abs(rec["contract_price"] - price) / price <= PRICE_TOLERANCE:
                    sim = name_similarity(contract.get("contract_name"), rec.get("work_name"))
                    if sim >= NAME_SIMILARITY_MIN:
                        yield i, sim

    for tier, exact in (("exact", True), ("fuzzy", False)):
        for contract in ours:
            if contract.get("_matched"):
                continue
            best = max(candidates(contract, exact), key=lambda x: x[1], default=None)
            if best is not None:
                used.add(best[0])
                contract["_matched"] = tier
                matches.append((contract, kiscon_records[best[0]], tier))

    unmatched_ours = [c for c in ours if not c.get("_matched")]
    unmatched_kiscon = [r for i, r in enumerate(kiscon_records) if i not in used]
    return matches, unmatched_ours, unmatched_kiscon


def reconcile_l2(ours, kiscon_records, months):
    # type: (List[dict], List[dict], List[str]) -> Tuple[List[dict], list, list]
    """L2 recon 행 (전체 창 단일 행) + Lincoln-Petersen 모집단 추정."""
    if not kiscon_records:
        return [], [], []
    matches, un_ours, un_kiscon = match_records(ours, kiscon_records)
    n1, n2, m = len(ours), len(kiscon_records), len(matches)
    n_hat = round(n1 * n2 / m) if m else None
    detail = ("매칭 {}건 (완전 {} / 유사 {}) · 커버리지 우리 {} KISCON {}".format(
        m,
        sum(1 for *_, t in matches if t == "exact"),
        sum(1 for *_, t in matches if t == "fuzzy"),
        "{:.1%}".format(n1 / n_hat) if n_hat else "-",
        "{:.1%}".format(n2 / n_hat) if n_hat else "-",
    ))
    row = {
        "ym": "{}~{}".format(min(months), max(months)) if months else "-",
        "level": "L2", "basis": "lag_adjusted",
        "ours_krw": sum(c["contract_price"] for c in ours),
        "kiscon_krw": sum(r["contract_price"] for r in kiscon_records
                          if r.get("contract_price")),
        "ratio": None, "n_ours": n1, "n_kiscon": n2,
        "n_matched": m, "n_hat": n_hat,
        "flag": None, "detail": detail,
    }
    return [row], un_ours, un_kiscon


# ---------------------------------------------------------------------- #
# 리포트                                                                    #
# ---------------------------------------------------------------------- #

_CSV_COLS = ["ym", "level", "basis", "ours_krw", "kiscon_krw", "ratio",
             "n_ours", "n_kiscon", "n_matched", "n_hat", "flag", "detail"]


def _df_to_markdown(df):
    """pandas.to_markdown은 tabulate 의존성이 필요해 직접 렌더링한다."""
    lines = ["| " + " | ".join(_CSV_COLS) + " |",
             "|" + "---|" * len(_CSV_COLS)]
    for _, row in df.iterrows():
        cells = []
        for c in _CSV_COLS:
            v = row[c]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                cells.append("-")
            elif c == "ratio" and isinstance(v, float):
                cells.append("{:.3f}".format(v))
            elif isinstance(v, float) and v.is_integer():
                cells.append("{:,}".format(int(v)))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_report(rows, loose_totals, output_dir, label):
    # type: (List[dict], Dict[str, dict], str, str) -> List[Path]
    """recon 행 → CSV + Markdown. 로그에도 전문 출력 (유일한 회수 경로)."""
    import pandas as pd

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=_CSV_COLS)
    csv_path = out / "kiscon_recon_{}.csv".format(label)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    md_lines = [
        "# KISCON 대조 리포트 ({})".format(label),
        "",
        "- ratio = 우리 DB(100억↑ 비교가능 모집단) ÷ KISCON(공공×원도급 전체 금액대)",
        "- 불변식: ratio < 1. `RATIO_GE_1`은 이중계상/정렬 실패를 의미한다.",
        "- KISCON 금액은 억원 단위 집계라 반올림 오차가 있다 (월·전국 규모에서 <1%).",
        "- 업종 제외(전기·정보통신·소방)는 공사명 키워드 휴리스틱이다.",
        "",
        _df_to_markdown(df),
        "",
        "## 업종 제외 미적용 대비 (휴리스틱 손실 가시화)",
    ]
    for ym in sorted(loose_totals):
        strict = next((r for r in rows
                       if r["ym"] == ym and r["level"] == "L0_AMT"
                       and r["basis"] == "contract_month"), None)
        if strict:
            md_lines.append("- {}: 제외 후 {:,} / 제외 전 {:,} 원".format(
                ym, strict["ours_krw"] or 0, loose_totals[ym]["krw"]))
    md_path = out / "kiscon_recon_{}.md".format(label)
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    logger.info("KISCON 대조 결과:\n%s", df.to_string(index=False))
    return [csv_path, md_path]


def write_unmatched(un_ours, un_kiscon, output_dir, label):
    # type: (list, list, str, str) -> None
    import pandas as pd
    out = Path(output_dir)
    if un_ours:
        pd.DataFrame(un_ours).drop(columns=["_matched"], errors="ignore").to_csv(
            out / "kiscon_unmatched_ours_{}.csv".format(label),
            index=False, encoding="utf-8-sig")
    if un_kiscon:
        pd.DataFrame(un_kiscon).to_csv(
            out / "kiscon_unmatched_kiscon_{}.csv".format(label),
            index=False, encoding="utf-8-sig")


# ---------------------------------------------------------------------- #
# 실행                                                                      #
# ---------------------------------------------------------------------- #

def run(db_path, month=None, skip_fetch=False, full_backfill=False, output_dir="output"):
    # type: (str, Optional[str], bool, bool, str) -> int
    conn = get_connection(db_path)
    ensure_schema(conn)

    ours = our_monthly_totals(conn, strict=True)
    loose = our_monthly_totals(conn, strict=False)
    months = [month] if month else sorted(ours)
    if not months:
        logger.warning("contracts에 비교가능 계약이 없습니다 — 대조 생략")
        return 0

    if not skip_fetch:
        from .kiscon import KisconClient, KISCON_DATA_START, collect_kiscon_stats, collect_kiscon_records
        client = KisconClient()
        if full_backfill:
            since = KISCON_DATA_START
        else:
            since = datetime.strptime(min(months), "%Y-%m").date()
        until = min(
            datetime.strptime(max(months), "%Y-%m").date()
            + relativedelta(months=2),   # 대상월 + 익월(통보 지연분)
            date.today(),
        )
        n_stats = collect_kiscon_stats(conn, client, since, until)
        n_records = collect_kiscon_records(conn, client, since, until)
        logger.info("KISCON 수집: 집계 %d행 / 건별 %d행 (%s ~ %s)", n_stats, n_records, since, until)

    kiscon = kiscon_monthly_totals(conn)
    rows = reconcile_l0(ours, kiscon, months)

    records = kiscon_record_rows(conn, months)
    l2_rows, un_ours, un_kiscon = reconcile_l2(
        our_universe_rows(conn, months, strict=True), records, months)
    rows.extend(l2_rows)

    upsert_kiscon_recon(conn, rows)
    label = (month or "{}_{}".format(min(months), max(months))).replace("-", "")
    write_report(rows, {ym: loose[ym] for ym in months if ym in loose}, output_dir, label)
    if l2_rows:
        write_unmatched(un_ours, un_kiscon, output_dir, label)

    flags = [r for r in rows if r["flag"]]
    for r in flags:
        logger.warning("플래그: %s %s/%s → %s (ratio=%s)",
                       r["ym"], r["level"], r["basis"], r["flag"], r["ratio"])
    return 1 if flags else 0


def main():
    # type: () -> int
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="KISCON 대조 검증")
    ap.add_argument("--db", default="procurement.db")
    ap.add_argument("--month", help="대상 월 YYYY-MM (생략 시 contracts의 모든 월)")
    ap.add_argument("--skip-fetch", action="store_true", help="KISCON 수집 생략, 대조만")
    ap.add_argument("--full-backfill", action="store_true",
                    help="2020-07-15부터 KISCON 통계 전체 백필")
    ap.add_argument("--output", default="output")
    args, _ = ap.parse_known_args()
    # run_monthly와 워크플로우 인자를 공유하므로 --month 외 인자는 무시한다.
    return run(args.db, month=args.month, skip_fetch=args.skip_fetch,
               full_backfill=args.full_backfill, output_dir=args.output)


if __name__ == "__main__":
    raise SystemExit(main())
