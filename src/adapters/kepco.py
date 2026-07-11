"""kepco.py — 한국전력공사(KEPCO) 어댑터

수집 경로 (실행계획 §2.1, 2026-07-03 사용자 제공 기술문서로 명세 확정):
  한전 빅데이터플랫폼 "전자입찰 계약정보" OpenAPI
  - 엔드포인트: https://bigdata.kepco.co.kr/openapi/v1/electContract.do
  - 인증: apiKey 쿼리 파라미터 (40자리) — KEPCO_API_KEY 환경변수
  - 필수 파라미터: noticeBeginDate/noticeEndDate (YYYYMMDD, 최대 90일)
  - 응답: JSON {"data": [...]} (returnType=json 고정 요청). 페이지네이션 없음.
  폴백: srm.kepco.net 비로그인 XHR — 실서비스 검증에서 커버리지 부족 확인 시 착수.

명세 확정 사항:
  - 본 API는 입찰공고 단일 엔드포인트다. 낙찰·계약 오퍼레이션은 제공되지 않으므로
    fetch_awards/fetch_contracts는 빈 결과를 반환하고, G2B 계약정보
    (dmndInsttNm=한국전력공사)로 보완한다 (실행계획 §8 리스크표 대응).
  - companyId로 회사를 구분한다. COM01=한전 외에 발전 자회사(COM02 서부,
    COM04 남부, COM05 중부, COM06 남동, COM08 동서)도 같은 API로 조회 가능
    → Phase 2 Wave B(발전 5사)에서 본 어댑터를 companyId만 바꿔 재사용 후보.
  - 추정가격(presumedPrice)은 국가계약법상 VAT 제외 금액이므로 환산 불필요.
  - 값이 없는 필드는 "-" 문자열로 오므로 None으로 정리한다.
"""
import hashlib
import json
import logging
import os
import time
from datetime import date, timedelta
from typing import Iterator, Optional

import requests

from .base import BaseProcurementAdapter
from ..http_client import get_with_retry
from ..long_term_detector import detect_long_term_from_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://bigdata.kepco.co.kr/openapi/v1/electContract.do"
# bigdata.kepco.co.kr는 단시간 연속 조회(발전 5사 순회 등)에 커넥션 리셋으로
# 레이트리밋을 건다 (run #30에서 3개사 연속 성공 후 4번째부터 reset 확인).
# 요청 간격을 넉넉히 잡고, 백오프도 리셋 해제까지 버티도록 길게 둔다.
REQUEST_INTERVAL = 5.0
MAX_RETRIES = 5
BACKOFF_BASE = 6.0  # 6/12/24/48초 — 총 90초까지 대기
MAX_RANGE_DAYS = 90      # noticeBeginDate~noticeEndDate 최대 조회 범위

COMPANY_KEPCO = "COM01"  # 한국전력공사
# Phase 2 Wave B 재사용 후보: 발전 자회사 companyId (기술문서 기준)
COMPANY_IDS = {
    "KEPCO":  "COM01",  # 한국전력공사
    "KOWEPO": "COM02",  # 한국서부발전
    "KOSPO":  "COM04",  # 한국남부발전
    "KOMIPO": "COM05",  # 한국중부발전
    "KOEN":   "COM06",  # 한국남동발전
    "EWP":    "COM08",  # 한국동서발전
}

# 코드값 → 한국어 매핑 (기술문서 세부내용 기준)
_ITEM_TYPE_MAP = {"Construction": "공사", "Service": "용역"}   # itemType 도급구분
_COMPETITION_MAP = {                                           # competitionType 계약방법
    "Open": "일반경쟁", "Destination": "지명경쟁",
    "Limited": "제한경쟁", "Private": "수의",
}
_PROGRESS_MAP = {                                              # progressState
    "PreAttendProgress": "공고진행", "AttendProgress": "입찰진행",
    "Close": "마감", "Fail": "유찰", "OpenTimed": "개찰", "Final": "공고종료",
}

# 장기계속 판별 대상 필드 (입찰건명·낙찰자결정방법설명·입찰참가자격)
_LT_KEYS = ["name", "bidTypeDetail", "etc"]


class KEPCOAdapter(BaseProcurementAdapter):
    """한전 빅데이터플랫폼 전자입찰 계약정보 OpenAPI 어댑터."""

    source = "kepco"
    agency_codes = ["KEPCO"]

    def __init__(self, service_key: Optional[str] = None, timeout: int = 30,
                 company_id: str = COMPANY_KEPCO):
        self.service_key = service_key or os.getenv("KEPCO_API_KEY", "")
        self.timeout = timeout
        self.company_id = company_id
        if not self.service_key:
            raise ValueError("KEPCO_API_KEY 환경변수 또는 service_key 인자 필요")
        self.session = requests.Session()

    # ── HTTP / 파싱 ────────────────────────────────────────────────────────

    def _get(self, params: dict) -> list:
        """단일 호출 → data 리스트. 페이지네이션은 제공되지 않는다."""
        query = {
            **params,
            "companyId": self.company_id,
            "apiKey": self.service_key,
            "returnType": "json",
        }
        try:
            r = get_with_retry(
                BASE_URL, query, timeout=self.timeout, session=self.session,
                max_retries=MAX_RETRIES, backoff_base=BACKOFF_BASE,
                sleep_before=REQUEST_INTERVAL, label="KEPCO",
            )
        except requests.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 404:
                # bigdata API는 조회 결과가 0건인 기간·회사 조합에 404를 반환한다
                # (2022-12-30~31 × COM06 실측 — 같은 구간 타사는 정상 응답).
                logger.info("[KEPCO] 404 응답 → 해당 구간 공고 없음 처리 (%s)", params)
                return []
            raise
        return self._parse_response(r.text)

    def _parse_response(self, text: str) -> list:
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            raise RuntimeError(f"KEPCO API 비JSON 응답: {(text or '')[:200]}")
        data = payload.get("data")
        if data is None:
            # {"data": ...} 규격 밖 응답은 인증 실패·오류 메시지로 간주
            raise RuntimeError(f"KEPCO API 오류 응답: {str(payload)[:200]}")
        if isinstance(data, dict):
            data = [data]
        return data or []

    def _date_chunks(self, since: date, until: date) -> Iterator[tuple]:
        """최대 90일 조회 제한 대응: 90일 단위로 분할."""
        cursor = since
        while cursor <= until:
            chunk_end = min(cursor + timedelta(days=MAX_RANGE_DAYS - 1), until)
            yield cursor, chunk_end
            cursor = chunk_end + timedelta(days=1)

    # ── Fetch ──────────────────────────────────────────────────────────────

    def fetch_notices(self, since: date, until: date) -> Iterator[dict]:
        """입찰공고 수집 (공고일 noticeBeginDate~noticeEndDate 기준).

        bigdata.kepco.co.kr는 간헐적으로 커넥션 리셋/타임아웃을 낸다. 재시도로도
        복구 못한 90일 창 하나가 연간 수집 전체를 무너뜨리지 않도록, 창 단위
        실패는 ERROR로 남기고 다음 창으로 진행한다(부분 수집이라도 회수).
        """
        failed = []
        for begin, end in self._date_chunks(since, until):
            rows = self._fetch_chunk(begin, end)
            if rows is None:
                failed.append((begin, end))
                continue
            logger.info("[KEPCO] 공고 %s~%s: %d건", begin, end, len(rows))
            yield from rows
        if failed:
            logger.error("[KEPCO] 조회 실패 구간 %d개 (부분 수집): %s",
                         len(failed), ", ".join("%s~%s" % (b, e) for b, e in failed))

    def _fetch_chunk(self, begin: date, end: date, attempts: int = 3):
        """한 90일 창을 조회. bigdata의 일시적 장애(커넥션 리셋·타임아웃·빈/비JSON
        응답)는 몇 차례 재시도 후에도 실패하면 None을 반환해 상위에서 스킵하게 한다.
        인증 오류 등 '오류 응답'은 설정 문제이므로 그대로 예외를 올린다."""
        params = {
            "noticeBeginDate": begin.strftime("%Y%m%d"),
            "noticeEndDate":   end.strftime("%Y%m%d"),
        }
        for i in range(attempts):
            try:
                return self._get(params)
            except (requests.ConnectionError, requests.Timeout):
                pass  # _get 내부 재시도로도 실패 → 창 단위로 한 번 더
            except RuntimeError as e:
                # 빈/비JSON 응답도 bigdata 일시 장애 → 재시도. '오류 응답'은 전파.
                if "비JSON" not in str(e):
                    raise
            if i < attempts - 1:
                time.sleep(REQUEST_INTERVAL * (i + 1))
        logger.error("[KEPCO] 공고 %s~%s 조회 실패(%d회 재시도) — 이 구간 스킵",
                     begin, end, attempts)
        return None

    def fetch_awards(self, since: date, until: date) -> Iterator[dict]:
        """낙찰결과 — 본 API 미제공 (명세 확정). G2B 계약정보로 보완한다."""
        logger.info("[KEPCO] 낙찰 오퍼레이션 미제공 — G2B(dmndInsttNm=한국전력공사)로 보완")
        return iter(())

    def fetch_contracts(self, since: date, until: date) -> Iterator[dict]:
        """계약체결현황 — 본 API 미제공 (명세 확정). G2B 계약정보로 보완한다."""
        logger.info("[KEPCO] 계약 오퍼레이션 미제공 — G2B(dmndInsttNm=한국전력공사)로 보완")
        return iter(())

    # ── Normalize ──────────────────────────────────────────────────────────

    def normalize(self, raw: dict) -> dict:
        notice_no = self._clean(raw.get("no"))
        # 차수 필드가 없으므로 1 고정 (notice_id = {source}:{공고번호}:{차수}).
        # source/agency_code는 클래스 속성 기준 — 발전 자회사 서브클래스
        # (kepco_family.py)가 companyId만 바꿔 그대로 재사용한다.
        return {
            "notice_id":               f"{self.source}:{notice_no}:1",
            "source":                  self.source,
            "notice_no":               notice_no,
            "notice_rev":              1,
            "agency_code":             self.agency_codes[0],
            "title":                   self._clean(raw.get("name")),
            "work_type":               self._work_type(raw),
            "construction_type":       self._construction_type(raw),
            "is_long_term_continuing": detect_long_term_from_raw(raw, _LT_KEYS),
            "bid_method":              self._bid_method(raw),
            # presumedPrice(추정가격)는 정의상 VAT 제외 → 환산 불필요
            "estimated_price":         self._to_int(raw.get("presumedPrice")),
            "vat_included":            False,
            "posted_at":               self._parse_dt(raw.get("noticeDate")),
            "bid_open_at":             self._parse_dt(raw.get("endDatetime")),
            "status":                  self._status(raw),
            "raw_payload":             raw,
            "source_hash":             self._hash(raw),
            "collected_at":            None,
            # 조인·검증용 부가 정보 (공통 스키마 외)
            "_place_name":             self._clean(raw.get("placeName")),      # 발주기관
            "_bid_type":               self._clean(raw.get("bidType")),        # 낙찰방법
            "_progress_state":         self._clean(raw.get("progressState")),
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    def _clean(self, v) -> Optional[str]:
        """빈값·'-' 플레이스홀더를 None으로 정리."""
        if v is None:
            return None
        s = str(v).strip()
        return s if s not in ("", "-") else None

    def _work_type(self, raw: dict) -> str:
        """itemType(도급구분)이 1차 기준. 없으면 purchaseType으로 판별."""
        item_type = self._clean(raw.get("itemType"))
        if item_type:
            return _ITEM_TYPE_MAP.get(item_type, item_type)
        purchase = self._clean(raw.get("purchaseType"))
        if purchase == "Product":
            return "물품"
        if purchase == "ConstructionService":
            # 공사/용역 통합 구분만 있을 때는 입찰건명으로 판별
            return "공사" if "공사" in (self._clean(raw.get("name")) or "") else "용역"
        return "미분류"

    def _construction_type(self, raw: dict) -> Optional[str]:
        text = " ".join(str(raw.get(k, "")) for k in ["name", "bidAttendRestrict", "etc"])
        if "전문" in text:
            return "전문"
        if "종합" in text:
            return "종합"
        return None

    def _bid_method(self, raw: dict) -> Optional[str]:
        code = self._clean(raw.get("competitionType"))
        if code:
            return _COMPETITION_MAP.get(code, code)
        return self._clean(raw.get("bidTypeDetail"))

    def _status(self, raw: dict) -> str:
        code = self._clean(raw.get("progressState"))
        if code:
            return _PROGRESS_MAP.get(code, code)
        return "공고중"

    def _to_int(self, v) -> Optional[int]:
        v = self._clean(v)
        if v is None:
            return None
        try:
            return int(float(v.replace(",", "").replace("원", "")))
        except (ValueError, TypeError):
            return None

    def _parse_dt(self, v) -> Optional[str]:
        """YYYYMMDD / YYYYMMDDHHMMSS → ISO 문자열."""
        v = self._clean(v)
        if v is None:
            return None
        digits = "".join(ch for ch in v if ch.isdigit())
        if len(digits) >= 14:
            return (f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
                    f"T{digits[8:10]}:{digits[10:12]}:{digits[12:14]}")
        if len(digits) >= 8:
            return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
        return None

    def _hash(self, raw: dict) -> str:
        """raw payload 해시 — 스키마 변경 모니터링용."""
        return hashlib.md5(
            json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def health_check(self) -> bool:
        """최근 1일 조회로 응답 확인 (0건이어도 규격 응답이면 정상)."""
        try:
            today = date.today()
            self._get({
                "noticeBeginDate": (today - timedelta(days=1)).strftime("%Y%m%d"),
                "noticeEndDate":   today.strftime("%Y%m%d"),
            })
            return True
        except Exception:
            return False
