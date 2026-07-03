"""kepco.py — 한국전력공사(KEPCO) 어댑터

수집 경로 (실행계획 §2.1):
  1차: 공공데이터포털 "한국전력공사_전자입찰계약정보" OpenAPI
       - 데이터셋: data.go.kr 15148223 (신), 3068324 (구)
       - 엔드포인트: http://openapi.kepco.co.kr/service/bidInfoService
       - 인증: KEPCO_API_KEY 환경변수 (공공데이터포털 발급 인증키)
  폴백: srm.kepco.net 비로그인 XHR 스크래핑 — 실서비스 검증에서 OpenAPI
       커버리지 부족이 확인될 때만 착수 (미구현).

※ 명세 확정 전 잠정 구현 (실행계획 §2.2 "명세 확정 절차"):
  개발 환경에서 data.go.kr·openapi.kepco.co.kr 접속이 차단되어 있어,
  오퍼레이션·파라미터·응답 필드명은 기술문서/실제 응답 수령 후 확정한다.
  - 필드명 후보는 _FIELD_CANDIDATES 한 곳에서 관리 (첫 매칭 키 사용)
  - 기간 파라미터명은 _PARAM_DATE_BEGIN/_PARAM_DATE_END 상수로 분리
  - 낙찰·계약 오퍼레이션은 존재 여부 미확인 → 기본 None (경고 후 빈 결과).
    미제공 확정 시 G2B 계약정보(dmndInsttNm=한국전력공사)로 보완한다.
  실제 응답 샘플은 tests/fixtures/kepco/ 에 배치해 테스트로 매핑을 검증한다.
"""
import hashlib
import json
import logging
import os
from datetime import date
from typing import Iterator, Optional, Tuple
from xml.etree import ElementTree as ET

import requests

from .base import BaseProcurementAdapter
from ..http_client import get_with_retry
from ..long_term_detector import detect_long_term_from_raw

logger = logging.getLogger(__name__)

BASE_URL = "http://openapi.kepco.co.kr/service/bidInfoService"
REQUEST_INTERVAL = 1.0   # 초당 최대 1 req (실행계획 §2.2)
MAX_RETRIES = 4
_MAX_PAGES = 2000        # 폭주 방지 상한 (totalCount 기준으로 그 전에 종료)

# 오퍼레이션명 — 공고 조회는 실행계획 §2.1에서 확정, 낙찰·계약은 기술문서
# 확인 전까지 None(미확인)으로 두고 호출 시 경고 후 빈 결과를 반환한다.
_NOTICE_OP: Optional[str] = "getBidSearchList"
_AWARD_OP: Optional[str] = None
_CONTRACT_OP: Optional[str] = None

# 기간 파라미터명 (잠정 — 기술문서 수령 후 확정)
_PARAM_DATE_BEGIN = "startDate"
_PARAM_DATE_END = "endDate"

# 응답 필드명 후보 — 각 항목은 우선순위순, 첫 번째로 존재하는 키를 쓴다.
# 실제 응답 샘플(fixture) 확보 후 이 표만 고치면 매핑이 확정된다.
_FIELD_CANDIDATES = {
    "notice_no":  ["bidNo", "bidNtceNo", "notiNo"],
    "notice_rev": ["bidDegree", "degree", "ord", "bidNtceOrd"],
    "title":      ["bidNm", "bidName", "notiNm", "bidNtceNm"],
    "work_div":   ["bidKindNm", "bizTypeNm", "workDivNm", "bsnsDivNm"],
    "bid_method": ["cntrctMthdNm", "bidMethodNm", "sunjungNm"],
    "price":      ["presmptPrc", "baseAmt", "bdgtAmt", "asignBdgtAmt"],
    "vat_yn":     ["vatYn", "vatIncldYn"],
    "posted_at":  ["notiDate", "bidRegDt", "registDt", "bidNtceDate"],
    "bid_open_at": ["openDate", "opengDt", "openDtm"],
    "status":     ["bidStatusNm", "progrsStatusNm", "bidProgrsStatus"],
}

# 장기계속 판별에 합쳐 볼 정규화 전 후보 키 (제목·계약방식 계열 전부)
_LT_KEYS = (_FIELD_CANDIDATES["title"] + _FIELD_CANDIDATES["bid_method"]
            + ["lngTmCntrctYn", "lngtrmCtnuDivNm"])


class KEPCOAdapter(BaseProcurementAdapter):
    """한국전력공사 전자입찰계약정보 OpenAPI 어댑터."""

    source = "kepco"
    agency_codes = ["KEPCO"]

    def __init__(self, service_key: Optional[str] = None, timeout: int = 30):
        self.service_key = service_key or os.getenv("KEPCO_API_KEY", "")
        self.timeout = timeout
        if not self.service_key:
            raise ValueError("KEPCO_API_KEY 환경변수 또는 service_key 인자 필요")
        self.session = requests.Session()

    # ── HTTP / 파싱 ────────────────────────────────────────────────────────

    def _get(self, operation: str, params: dict) -> Tuple[list, int]:
        """단일 페이지 호출 → (items, totalCount). XML 우선, JSON 응답도 지원."""
        url = f"{BASE_URL}/{operation}"
        query = {**params, "serviceKey": self.service_key}
        r = get_with_retry(
            url, query, timeout=self.timeout, session=self.session,
            max_retries=MAX_RETRIES, sleep_before=REQUEST_INTERVAL, label="KEPCO",
        )
        return self._parse_response(r.text)

    def _parse_response(self, text: str) -> Tuple[list, int]:
        text = (text or "").strip()
        if text.startswith("{"):
            return self._parse_json(text)
        return self._parse_xml(text)

    def _parse_xml(self, text: str) -> Tuple[list, int]:
        root = ET.fromstring(text)
        code = root.findtext(".//resultCode")
        if code not in (None, "00", "0"):
            msg = root.findtext(".//resultMsg") or ""
            raise RuntimeError(f"KEPCO API 오류 resultCode={code} {msg}")
        items = []
        for item in root.findall(".//item"):
            items.append({c.tag: (c.text or "").strip() for c in list(item)})
        total = int(root.findtext(".//totalCount") or len(items))
        return items, total

    def _parse_json(self, text: str) -> Tuple[list, int]:
        data = json.loads(text)
        body = data.get("response", {}).get("body", data.get("body", data))
        items = body.get("items", [])
        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]
        total = int(body.get("totalCount", len(items)) or 0)
        return items or [], total

    def _paginate(self, operation: str, params: dict, page_size: int = 100) -> Iterator[dict]:
        page = 1
        fetched = 0
        while True:
            items, total = self._get(operation, {**params, "numOfRows": page_size, "pageNo": page})
            for row in items:
                yield row
            fetched += len(items)
            if page == 1 and total > 1000:
                logger.info("[KEPCO] %s totalCount=%d — 대량 수집 시작", operation, total)
            if fetched >= total or not items:
                break
            if page >= _MAX_PAGES:
                logger.warning("[KEPCO] %s max_pages=%d 도달, 중단 (total=%d fetched=%d)",
                               operation, _MAX_PAGES, total, fetched)
                break
            page += 1

    # ── Fetch ──────────────────────────────────────────────────────────────

    def fetch_notices(self, since: date, until: date) -> Iterator[dict]:
        """입찰공고 수집 (getBidSearchList)."""
        yield from self._paginate(_NOTICE_OP, {
            _PARAM_DATE_BEGIN: since.strftime("%Y%m%d"),
            _PARAM_DATE_END:   until.strftime("%Y%m%d"),
        })

    def fetch_awards(self, since: date, until: date) -> Iterator[dict]:
        """낙찰결과 수집 — 오퍼레이션 확정 전까지 빈 결과 (실행계획 §2.2)."""
        if _AWARD_OP is None:
            logger.warning("[KEPCO] 낙찰 오퍼레이션 미확정 — 수집 생략 "
                           "(기술문서 확인 후 확정, 미제공 시 G2B 계약정보로 보완)")
            return
        yield from self._paginate(_AWARD_OP, {
            _PARAM_DATE_BEGIN: since.strftime("%Y%m%d"),
            _PARAM_DATE_END:   until.strftime("%Y%m%d"),
        })

    def fetch_contracts(self, since: date, until: date) -> Iterator[dict]:
        """계약체결현황 수집 — 오퍼레이션 확정 전까지 빈 결과 (실행계획 §2.2)."""
        if _CONTRACT_OP is None:
            logger.warning("[KEPCO] 계약 오퍼레이션 미확정 — 수집 생략 "
                           "(기술문서 확인 후 확정, 미제공 시 G2B 계약정보로 보완)")
            return
        yield from self._paginate(_CONTRACT_OP, {
            _PARAM_DATE_BEGIN: since.strftime("%Y%m%d"),
            _PARAM_DATE_END:   until.strftime("%Y%m%d"),
        })

    # ── Normalize ──────────────────────────────────────────────────────────

    def normalize(self, raw: dict) -> dict:
        notice_no = self._first(raw, "notice_no")
        rev = self._to_int(self._first(raw, "notice_rev")) or 1
        estimated_price, vat_included = self._estimated_price_vat_excl(raw)
        return {
            "notice_id":               f"kepco:{notice_no}:{rev}",
            "source":                  self.source,
            "notice_no":               notice_no,
            "notice_rev":              rev,
            "agency_code":             "KEPCO",
            "title":                   self._first(raw, "title"),
            "work_type":               self._work_type(raw),
            "construction_type":       self._construction_type(raw),
            "is_long_term_continuing": detect_long_term_from_raw(raw, _LT_KEYS),
            "bid_method":              self._first(raw, "bid_method"),
            "estimated_price":         estimated_price,
            "vat_included":            vat_included,
            "posted_at":               self._parse_dt(self._first(raw, "posted_at")),
            "bid_open_at":             self._parse_dt(self._first(raw, "bid_open_at")),
            "status":                  self._first(raw, "status") or "공고중",
            "raw_payload":             raw,
            "source_hash":             self._hash(raw),
            "collected_at":            None,
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    def _first(self, raw: dict, field: str) -> Optional[str]:
        """_FIELD_CANDIDATES 우선순위에 따라 첫 번째 존재하는 값을 반환."""
        for key in _FIELD_CANDIDATES[field]:
            v = raw.get(key)
            if v not in (None, ""):
                return v
        return None

    def _estimated_price_vat_excl(self, raw: dict) -> Tuple[Optional[int], bool]:
        """추정가격(VAT 제외) 반환. VAT 포함 표기이면 /1.1 환산 (g2b_opnstd 패턴)."""
        amt = self._to_int(self._first(raw, "price"))
        if amt is None:
            return None, False
        vat_included = str(self._first(raw, "vat_yn") or "").upper() == "Y"
        if vat_included:
            amt = int(amt / 1.1)
        return amt, vat_included

    def _work_type(self, raw: dict) -> str:
        """업무구분 필드(없으면 공고명)에 '공사'가 있으면 공사로 판별."""
        div = str(self._first(raw, "work_div") or "")
        if div:
            return "공사" if "공사" in div else div
        title = str(self._first(raw, "title") or "")
        return "공사" if "공사" in title else "미분류"

    def _construction_type(self, raw: dict) -> Optional[str]:
        text = " ".join(str(raw.get(k, "")) for k in
                        _FIELD_CANDIDATES["work_div"] + _FIELD_CANDIDATES["title"])
        if "전문" in text:
            return "전문"
        if "종합" in text:
            return "종합"
        return None

    def _to_int(self, v) -> Optional[int]:
        if v in (None, "", "-"):
            return None
        try:
            return int(float(str(v).replace(",", "").replace("원", "").strip()))
        except (ValueError, TypeError):
            return None

    def _parse_dt(self, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v = str(v).strip().replace(" ", "T")
        return v if len(v) >= 8 else None

    def _hash(self, raw: dict) -> str:
        """raw payload 해시 — 스키마 변경 모니터링용 (lh._hash 패턴)."""
        return hashlib.md5(
            json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def health_check(self) -> bool:
        """최소 조회 1건으로 응답 확인."""
        try:
            today = date.today()
            self._get(_NOTICE_OP, {
                "numOfRows": 1, "pageNo": 1,
                _PARAM_DATE_BEGIN: today.strftime("%Y%m%d"),
                _PARAM_DATE_END:   today.strftime("%Y%m%d"),
            })
            return True
        except Exception:
            return False
