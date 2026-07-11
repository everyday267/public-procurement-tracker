"""validate_kosis.py — 수집 데이터 ↔ KOSIS 건설업 통계 대조 (연간 상한 sanity check).

설계 문서 §4.1: KOSIS는 모집단·정의가 우리 DB와 달라 정밀 대조엔 부적합하고,
**상한 sanity check + 증감률 정합성**으로만 쓴다. 다만 KISCON StatAmt에 없던
공사규모(금액구간) 축을 제공하므로, "우리 100억↑ 공공 계약액"의 상한/커버리지
감을 산업별로 잡을 수 있다.

대조 대상 (연도별):
  우리 DB 100억↑ 공공 계약액(비교가능 모집단, 전기·정보통신·소방 제외)
    vs KOSIS 종합건설업 100억↑ 공공 계약액 (gen, 범위형 구간 합산)

해석 주의:
  - 우리 모집단은 종합+전문 혼합인데 KOSIS 전문(spec)은 최대 구간이 '50억이상'
    이라 100억↑ 구간이 없다(전문 100억↑는 KOSIS로 측정 불가). 따라서 우리 값은
    KOSIS 종합 100억↑에 '전문 100억↑'만큼 더 크게 나오는 게 정상 → ratio ≳ 1.
  - KOSIS 계약통계는 발주자 신고 기반 연간 집계라 우리 계약일 기준과 시점·정의가
    다르다. 정밀 일치가 아니라 자릿수·추세 확인용이다.

플래그(넓은 밴드):
  RATIO_LOW  : ratio < 0.5  → 대량 미수집 의심
  RATIO_HIGH : ratio > 3.0  → 이중계상/과대수집 의심
  NO_KOSIS   : 해당 연도 KOSIS 값 없음
"""
import argparse
import logging
from pathlib import Path
from typing import Dict, List, Optional

from .db import get_connection, ensure_schema, upsert_kosis_recon
from .kosis import ge100_public_by_year, PUBLIC_AGENCIES
from .validate_kiscon import _UNIVERSE_SQL, _STRICT_EXCLUDE

logger = logging.getLogger("validate_kosis")

RATIO_LOW, RATIO_HIGH = 0.5, 3.0
# KOSIS 종합 대조를 위한 기본 산업 (전문은 100억↑ 구간 부재, 전기는 대상외)
PRIMARY_INDUSTRY = "종합"


def our_annual_public_100eok(conn, strict=True):
    # type: (object, bool) -> Dict[str, int]
    """우리 DB 비교가능 모집단의 연도별 계약액 합계(원). 100억↑는 수집 단계에서
    이미 필터됨(CONSTRUCTION_MIN_PRICE). 우리 소스는 전부 공공발주라 공공=전체."""
    sql = _UNIVERSE_SQL + (_STRICT_EXCLUDE if strict else "")
    cur = conn.execute(
        "SELECT substr(contracted_at,1,4) AS y, SUM(contract_price) AS krw "
        "FROM ({}) GROUP BY y".format(sql)
    )
    return {r["y"]: int(r["krw"]) for r in cur.fetchall() if r["y"]}


def reconcile(ours, kosis_by_year, industry=PRIMARY_INDUSTRY):
    # type: (Dict[str, int], Dict[str, float], str) -> List[dict]
    """연도별 recon 행 생성. 대상 연도는 우리 DB에 계약이 있는 연도."""
    rows = []
    for year in sorted(ours):
        ours_krw = ours[year]
        kosis_krw = kosis_by_year.get(year)
        ratio = (ours_krw / kosis_krw) if kosis_krw else None
        if kosis_krw is None:
            flag = "NO_KOSIS"
        elif ratio is not None and ratio < RATIO_LOW:
            flag = "RATIO_LOW"
        elif ratio is not None and ratio > RATIO_HIGH:
            flag = "RATIO_HIGH"
        else:
            flag = None
        rows.append({
            "year": year, "industry": industry,
            "ours_krw": ours_krw,
            "kosis_krw": int(kosis_krw) if kosis_krw is not None else None,
            "ratio": ratio, "flag": flag,
            "detail": "우리(종합+전문 100억↑ 공공) vs KOSIS 종합 100억↑ 공공. "
                      "우리는 전문 100억↑ 포함이라 ratio≳1 정상.",
        })
    return rows


def write_report(rows, ref, output_dir, label):
    # type: (List[dict], Dict[str, dict], str, str) -> List[Path]
    """recon 행 → CSV + Markdown, 로그 전문 출력."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cols = ["year", "industry", "ours_krw", "kosis_krw", "ratio", "flag", "detail"]

    csv_lines = [",".join(cols)]
    for r in rows:
        csv_lines.append(",".join(
            "" if r[c] is None else
            ("{:.4f}".format(r[c]) if c == "ratio" else str(r[c]).replace(",", " "))
            for c in cols))
    csv_path = out / "kosis_recon_{}.csv".format(label)
    csv_path.write_text("\n".join(csv_lines), encoding="utf-8-sig")

    md = ["# KOSIS 대조 리포트 ({})".format(label), "",
          "연간 상한 sanity check — 우리 100억↑ 공공 계약액 vs KOSIS 종합 100억↑ 공공.",
          "정밀 일치가 아니라 자릿수·추세 확인용(설계 §4.1).", "",
          "| 연도 | 우리(원) | KOSIS 종합(원) | ratio | flag |",
          "|---|---|---|---|---|"]
    for r in rows:
        md.append("| {} | {:,} | {} | {} | {} |".format(
            r["year"], r["ours_krw"],
            "{:,}".format(r["kosis_krw"]) if r["kosis_krw"] is not None else "-",
            "{:.3f}".format(r["ratio"]) if r["ratio"] is not None else "-",
            r["flag"] or ""))
    md += ["", "## 참고: KOSIS 산업별 100억↑ 공공 (연도별)"]
    for ind, by_year in ref.items():
        md.append("- **{}**: {}".format(ind, ", ".join(
            "{}={:,}원".format(y, int(v)) for y, v in sorted(by_year.items()))))
    md_path = out / "kosis_recon_{}.md".format(label)
    md_path.write_text("\n".join(md), encoding="utf-8")

    logger.info("KOSIS 대조:\n%s", "\n".join(md))
    return [csv_path, md_path]


def run(db_path, output_dir="output", strict=True):
    # type: (str, str, bool) -> int
    conn = get_connection(db_path)
    ensure_schema(conn)

    ours = our_annual_public_100eok(conn, strict=strict)
    if not ours:
        logger.warning("contracts에 비교가능 계약이 없습니다 — KOSIS 대조 생략")
        return 0

    kosis_gen = ge100_public_by_year(conn, "종합")
    rows = reconcile(ours, kosis_gen, industry="종합")
    upsert_kosis_recon(conn, rows)

    # 참고용: 산업별 100억↑ 공공 (있는 것만)
    ref = {}
    for ind in ("종합", "전기"):
        by_year = ge100_public_by_year(conn, ind)
        if any(by_year.values()):
            ref[ind] = by_year

    label = "{}_{}".format(min(ours), max(ours)) if ours else "na"
    write_report(rows, ref, output_dir, label)

    flags = [r for r in rows if r["flag"]]
    for r in flags:
        logger.warning("플래그: %s %s → %s (ratio=%s)",
                       r["year"], r["industry"], r["flag"], r["ratio"])
    return 1 if flags else 0


def main():
    # type: () -> int
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="KOSIS 연간 대조 검증")
    ap.add_argument("--db", default="procurement.db")
    ap.add_argument("--output", default="output")
    args, _ = ap.parse_known_args()
    return run(args.db, output_dir=args.output)


if __name__ == "__main__":
    raise SystemExit(main())
