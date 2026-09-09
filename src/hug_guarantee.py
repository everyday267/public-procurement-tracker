"""hug_guarantee.py — HUG(주택도시보증공사) 분양보증현황 조회 클라이언트.

사용자 제공 프로그램사양서(`HUG-PA_프로그램사양서` v0.7, 2019-03-11)의
**[OPEN API] 지역별 분양보증현황**(프로그램ID `AreaBusinessDistributionGuaranteeStatus`)
명세를 그대로 구현한다.

  GET https://www.khug.or.kr/areaBusinessDistributionGuaranteeStatus.do
      ?API_KEY=<인증키>&AREA_DCD=<지역>    → 지역별 사업장 목록
      ?API_KEY=<인증키>&BUNO=<사업장번호>   → 사업장 상세

응답(XML):
    <response>
      <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
      <body><items>
        <item><BUNO/><BSU_NM/><CUST_NM1/><CUST_NM2/><ADDR/></item> ...
      </items></body>
    </response>

⚠️ **오피스텔·상가 판정에 관한 한계**
사양서의 목록 응답 필드는 사업장번호·사업장명·시행업체명·시공업체명·주소 5개뿐이라
**주택유형(아파트/오피스텔/상가) 구분 필드가 없다.** 따라서 이 모듈은

  1) 응답에 유형성 필드가 있으면(상세 조회 응답 등) 그 값으로 판정하고
     (`basis="field:<필드명>"`, 확정),
  2) 없으면 사업장명 키워드로 추정한다 (`basis="name"`, 추정 — 오탐 가능).

상세(BUNO) 응답의 실제 필드는 사양서에 명시가 없어 파서가 **미지 필드를 모두
보존**하도록 구현했다. 실제 필드 구성은 `scripts/probe_hug_api.py`로 확정한다.
"""
import argparse
import csv
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional

import requests

from .http_client import get_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://www.khug.or.kr/areaBusinessDistributionGuaranteeStatus.do"

RESULT_CODE_OK = "00"

# 사양서 목록 응답 필드 (순서 고정 — CSV 컬럼 순서로도 사용)
LIST_FIELDS = ["BUNO", "BSU_NM", "CUST_NM1", "CUST_NM2", "ADDR"]

# AREA_DCD 코드값은 사양서에 목록이 없고 샘플로 "02"만 제시돼 있다.
# 실제 유효 코드는 probe로 확정해야 하므로 후보 범위를 순회한다(00~30).
CANDIDATE_AREA_CODES = [f"{i:02d}" for i in range(0, 31)]

# ── 유형 분류 ────────────────────────────────────────────────────────────
CAT_APARTMENT = "apartment"
CAT_OFFICETEL = "officetel"
CAT_RETAIL = "retail"
CAT_MIXED = "mixed"
CAT_UNKNOWN = "unknown"

CATEGORY_LABELS = {
    CAT_APARTMENT: "아파트",
    CAT_OFFICETEL: "오피스텔",
    CAT_RETAIL: "상가",
    CAT_MIXED: "주상복합",
    CAT_UNKNOWN: "미상",
}

# 판정 키워드. 앞쪽(오피스텔·상가)을 먼저 검사해 "○○아파트 상가" 같은
# 복합 표기에서 상가가 아파트에 묻히지 않게 한다.
CATEGORY_KEYWORDS = [
    (CAT_OFFICETEL, ("오피스텔",)),
    (CAT_RETAIL, ("상가", "근린생활시설", "복리시설", "판매시설")),
    (CAT_MIXED, ("주상복합",)),
    (CAT_APARTMENT, ("아파트", "도시형생활주택", "주택")),
]

# 사업장 식별 정보 필드 — 유형 '확정' 근거로 쓰면 안 된다
# (예: 시행사명에 "상가"가 들어갈 수 있음). 이 필드들은 추정에만 사용한다.
IDENTITY_FIELDS = {"BUNO", "BSU_NM", "CUST_NM1", "CUST_NM2", "ADDR"}


class HugApiError(RuntimeError):
    """resultCode가 정상(00)이 아닐 때."""

    def __init__(self, result_code: str, result_msg: str):
        super().__init__(f"HUG API 오류: resultCode={result_code} resultMsg={result_msg}")
        self.result_code = result_code
        self.result_msg = result_msg


class ParsedResponse(NamedTuple):
    result_code: str
    result_msg: str
    items: List[Dict[str, str]]


def parse_response(xml_text: str) -> ParsedResponse:
    """응답 XML → (resultCode, resultMsg, items).

    item의 자식 태그를 그대로 dict로 옮긴다(미지 필드 보존). 태그 경로 대신
    트리 전체에서 item을 찾고, XML이 깨져 있으면(사양서 응답 예제도 `</items>`가
    빠져 있다) 정규식으로 item 블록만 건져내는 완화 파싱으로 폴백한다.
    """
    text = xml_text.strip()
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        logger.warning("[HUG] XML 파싱 실패 — 완화 파싱으로 폴백: %s", e)
        return _parse_response_lenient(text)

    return ParsedResponse(_find_text(root, "resultCode"),
                          _find_text(root, "resultMsg"),
                          [_item_dict(el) for el in root.iter("item")
                           if len(el)])


def _item_dict(el: ET.Element) -> Dict[str, str]:
    return {child.tag: (child.text or "").strip() for child in el}


def _find_text(root: ET.Element, tag: str) -> str:
    el = root.find(f".//{tag}")
    return (el.text or "").strip() if el is not None and el.text else ""


def _tag_text(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return m.group(1).strip() if m else ""


def _parse_response_lenient(text: str) -> ParsedResponse:
    """닫는 태그 누락 등으로 트리 파싱이 실패했을 때의 폴백."""
    items = []
    for block in re.findall(r"<item>.*?</item>", text, re.S):
        try:
            items.append(_item_dict(ET.fromstring(block)))
        except ET.ParseError:
            logger.warning("[HUG] item 블록 파싱 실패 — 건너뜀")
    return ParsedResponse(_tag_text(text, "resultCode"),
                          _tag_text(text, "resultMsg"),
                          [i for i in items if i])


def classify_item(item: Dict[str, str]) -> tuple:
    """사업장 dict → (category, basis).

    basis: "field:<필드명>"(응답의 유형 필드로 확정) | "name"(사업장명 추정) | ""
    """
    # 1) 식별 필드가 아닌 필드에 유형 키워드가 있으면 확정 근거로 본다.
    for key, value in item.items():
        if key in IDENTITY_FIELDS or not value:
            continue
        cat = _match_category(value)
        if cat:
            return cat, f"field:{key}"

    # 2) 사업장명 키워드 추정 (오탐 가능 — 확정 아님)
    cat = _match_category(item.get("BSU_NM", ""))
    if cat:
        return cat, "name"

    return CAT_UNKNOWN, ""


def _match_category(text: str) -> Optional[str]:
    for category, keywords in CATEGORY_KEYWORDS:
        if any(kw in text for kw in keywords):
            return category
    return None


class HugGuaranteeClient:
    """분양보증현황(지역) OpenAPI 클라이언트."""

    def __init__(self, api_key: Optional[str] = None, timeout: int = 30,
                 request_interval: float = 0.5):
        self.api_key = api_key or os.getenv("HUG_API_KEY", "")
        if not self.api_key:
            raise ValueError("HUG_API_KEY 환경변수 또는 api_key 인자 필요")
        self.timeout = timeout
        self.request_interval = request_interval
        self.session = requests.Session()

    # ── 요청 ──────────────────────────────────────────────────────────────

    def _get(self, params: dict, label: str) -> ParsedResponse:
        resp = get_with_retry(
            BASE_URL, {"API_KEY": self.api_key, **params},
            timeout=self.timeout, session=self.session,
            sleep_before=self.request_interval, label=label,
        )
        parsed = parse_response(resp.text)
        if parsed.result_code and parsed.result_code != RESULT_CODE_OK:
            raise HugApiError(parsed.result_code, parsed.result_msg)
        return parsed

    def fetch_area(self, area_dcd: str) -> List[Dict[str, str]]:
        """AREA_DCD 하나의 사업장 목록. 각 행에 `AREA_DCD`를 덧붙인다."""
        parsed = self._get({"AREA_DCD": area_dcd}, label=f"HUG area={area_dcd}")
        logger.info("[HUG] AREA_DCD=%s → %d건", area_dcd, len(parsed.items))
        return [{"AREA_DCD": area_dcd, **item} for item in parsed.items]

    def fetch_areas(self, area_codes: Iterable[str],
                    skip_errors: bool = True) -> List[Dict[str, str]]:
        """여러 지역 순회 수집. 유효하지 않은 코드는 건너뛴다(skip_errors)."""
        rows = []
        for code in area_codes:
            try:
                rows.extend(self.fetch_area(code))
            except (HugApiError, requests.RequestException) as e:
                if not skip_errors:
                    raise
                logger.warning("[HUG] AREA_DCD=%s 조회 실패 — 건너뜀: %s", code, e)
        return rows

    def fetch_detail(self, buno: str) -> Optional[Dict[str, str]]:
        """사업장번호(BUNO) 상세. 응답 필드는 사양서 미명시 → 그대로 보존."""
        parsed = self._get({"BUNO": buno}, label=f"HUG buno={buno}")
        return parsed.items[0] if parsed.items else None

    def enrich_with_detail(self, rows: List[Dict[str, str]],
                           limit: Optional[int] = None) -> List[Dict[str, str]]:
        """각 행에 상세 응답 필드를 병합한다(목록에 없는 필드만 추가)."""
        out = []
        for i, row in enumerate(rows):
            if limit is not None and i >= limit:
                out.extend(rows[i:])
                break
            merged = dict(row)
            buno = row.get("BUNO", "")
            if buno:
                try:
                    detail = self.fetch_detail(buno)
                except (HugApiError, requests.RequestException) as e:
                    logger.warning("[HUG] BUNO=%s 상세 실패: %s", buno, e)
                    detail = None
                for k, v in (detail or {}).items():
                    merged.setdefault(k, v)
            out.append(merged)
        return out


# ── 집계 / 출력 ──────────────────────────────────────────────────────────

def annotate(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """각 행에 category / category_label / category_basis 컬럼을 추가."""
    out = []
    for row in rows:
        category, basis = classify_item(row)
        out.append({**row,
                    "category": category,
                    "category_label": CATEGORY_LABELS[category],
                    "category_basis": basis})
    return out


def dedupe_by_buno(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """사업장번호 중복 제거(첫 행 유지).

    AREA_DCD 코드 체계가 미확정이라 후보 코드를 순회하면 같은 사업장이 여러 번
    잡힐 수 있다(예: 무효 코드가 전체 목록을 돌려주는 경우).
    """
    seen = set()
    out = []
    for row in rows:
        buno = row.get("BUNO", "")
        if buno and buno in seen:
            continue
        seen.add(buno)
        out.append(row)
    return out


def summarize(rows: List[Dict[str, str]]) -> Counter:
    return Counter(row.get("category", CAT_UNKNOWN) for row in rows)


def write_csv(rows: List[Dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = ["AREA_DCD"] + LIST_FIELDS + ["category", "category_label", "category_basis"]
    extras = [k for k in _all_keys(rows) if k not in head]
    fieldnames = head + extras
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _all_keys(rows: List[Dict[str, str]]) -> List[str]:
    keys = []
    for row in rows:
        for k in row:
            if k not in keys:
                keys.append(k)
    return keys


# ── CLI ──────────────────────────────────────────────────────────────────

def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="python -m src.hug_guarantee",
        description="HUG 지역별 분양보증현황 조회 (오피스텔·상가 필터 지원)")
    p.add_argument("--areas", default="all",
                   help="AREA_DCD 목록 (쉼표구분). 'all'이면 00~30 후보 전체 순회 "
                        f"(기본: all). 예: --areas 02,03")
    p.add_argument("--categories", default="officetel,retail",
                   help="출력할 유형 (쉼표구분: officetel,retail,apartment,mixed,unknown). "
                        "'all'이면 전체 (기본: officetel,retail)")
    p.add_argument("--detail", action="store_true",
                   help="사업장별 상세(BUNO) 조회로 필드 보강 — 요청 수가 사업장 수만큼 늘어난다")
    p.add_argument("--detail-limit", type=int, default=None,
                   help="--detail 조회를 앞 N건으로 제한 (스모크 테스트용)")
    p.add_argument("--out", default=None,
                   help="CSV 출력 경로 (기본: output/hug_guarantee_YYYYMMDD.csv)")
    p.add_argument("--interval", type=float, default=0.5, help="요청 간 간격(초)")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")

    areas = (CANDIDATE_AREA_CODES if args.areas == "all"
             else [a.strip() for a in args.areas.split(",") if a.strip()])
    wanted = (set(CATEGORY_LABELS) if args.categories == "all"
              else {c.strip() for c in args.categories.split(",") if c.strip()})
    unknown_cats = wanted - set(CATEGORY_LABELS)
    if unknown_cats:
        print(f"알 수 없는 유형: {', '.join(sorted(unknown_cats))}", file=sys.stderr)
        return 2

    try:
        client = HugGuaranteeClient(request_interval=args.interval)
    except ValueError as e:
        print(f"{e}\nHUG OPEN API 서비스키를 발급받아 HUG_API_KEY로 설정하세요.",
              file=sys.stderr)
        return 2

    if args.areas == "all":
        logger.info("[HUG] AREA_DCD 코드표가 사양서에 없어 %s~%s 후보를 순회합니다 "
                    "(응답 없는 코드는 건너뜀).",
                    CANDIDATE_AREA_CODES[0], CANDIDATE_AREA_CODES[-1])

    rows = client.fetch_areas(areas)
    raw_count = len(rows)
    rows = dedupe_by_buno(rows)
    if raw_count != len(rows):
        logger.info("[HUG] 사업장번호 중복 %d건 제거", raw_count - len(rows))
    if args.detail:
        rows = client.enrich_with_detail(rows, limit=args.detail_limit)
    rows = annotate(rows)

    total = summarize(rows)
    selected = [r for r in rows if r["category"] in wanted]

    out_path = Path(args.out) if args.out else Path(
        f"output/hug_guarantee_{date.today():%Y%m%d}.csv")
    write_csv(selected, out_path)

    print(f"\n수집 사업장 {len(rows)}건 (지역 {len(areas)}개 순회)")
    for cat in [CAT_APARTMENT, CAT_OFFICETEL, CAT_RETAIL, CAT_MIXED, CAT_UNKNOWN]:
        mark = "*" if cat in wanted else " "
        print(f" {mark} {CATEGORY_LABELS[cat]:<6} {total.get(cat, 0):>6}건")
    guessed = sum(1 for r in selected if r["category_basis"] == "name")
    print(f"\n선택 유형 {len(selected)}건 → {out_path}")
    if guessed:
        print(f"⚠️ 이 중 {guessed}건은 사업장명 키워드 '추정'입니다 "
              f"(목록 API에 주택유형 필드가 없음 — category_basis 컬럼 확인).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
