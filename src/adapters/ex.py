"""ex.py — 한국도로공사(EX) 어댑터 (Phase 2 Wave A)

수집 경로 (2026-07-04 사용자 제공 기술문서로 명세 확정):
  EX 자체 공공데이터포털 "전자조달 계약공개현황" OpenAPI
  - Request URL: https://data.ex.co.kr/openapi/elctPrcmInfo/elctPrcmCntrtOppubPrss
  - HTTPS GET, key(=EX_API_KEY, 10자리)·type(json) 필수
  - 기간: sCntrtCntgDates/eCntrtCntgDates (계약체결일자 범위, YYYYMMDD)
  - 페이징: pageNo/numOfRows, 응답 count(전체 건수)

※ 이 API는 **계약(체결일 기준) 데이터**다. 입찰공고 API가 아니므로:
  - fetch_contracts가 1차 수집 경로 (체결일 기준 100억↑ 공사계약 = 핵심 산출물)
  - fetch_notices는 빈 결과 — EX 입찰공고는 ebid.ex.co.kr 포털 XHR
    (findPagingPortalBidNotiList.do, 3차 조사에서 확보)로 후속 구현 예정
  - fetch_awards는 미제공 → 계약업체(cntrtCrprNm)가 사실상 낙찰자 역할

응답 필드(기술문서): pbanClssCd/pbanClssNm(공고구분: CT=공사, SV=용역, MT=물품,
CS=건설안전점검 등 13종), scbdPbanNo(공고번호), cntrtNm(계약명),
cmpttMthd(계약방법), crno(사업자등록번호), cntrtCrprNm(계약업체명),
cntrtAmt(계약금액), cntrtDptnm(계약부서명), sprvDptnm(주관부서명),
cntrtCntgDates(계약체결일자) + code/message/count/pageNo/numOfRows

※ 응답 JSON의 목록 키 이름·code 성공값은 문서에 명시가 없어 방어적으로
  파싱한다(_extract_rows). 실서비스 검증에서 확정 후 단순화 가능.
"""
import logging
import os
from datetime import date
from typing import Iterator, Optional

from .scraper_base import ScraperBaseAdapter
from ..long_term_detector import detect_long_term_from_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://data.ex.co.kr/openapi/elctPrcmInfo/elctPrcmCntrtOppubPrss"
PAGE_SIZE = 100
_MAX_PAGES = 500

# 공고구분코드 → 공사 여부. CT(공사)가 핵심, CS(건설안전점검)는 용역성이라 제외.
_CONSTRUCTION_CODES = {"CT"}
_LT_KEYS = ["cntrtNm", "cmpttMthd"]


class EXAdapter(ScraperBaseAdapter):
    """한국도로공사 전자조달 계약공개현황 OpenAPI 어댑터 (계약 중심)."""

    source = "ex"
    agency_codes = ["EX"]
    request_interval = 1.0  # OpenAPI라 스크래핑 기본(2초)보다 완화

    def __init__(self, service_key: Optional[str] = None, timeout: int = 30):
        super().__init__(timeout=timeout)
        self.service_key = service_key or os.getenv("EX_API_KEY", "")
        if not self.service_key:
            raise ValueError("EX_API_KEY 환경변수 또는 service_key 인자 필요")

    # ── fetch ─────────────────────────────────────────────────────────────

    def fetch_contracts(self, since: date, until: date) -> Iterator[dict]:
        """계약체결현황 수집 (계약체결일자 범위, 페이지네이션)."""
        page = 1
        fetched = 0
        while True:
            payload = self.get_json(BASE_URL, {
                "key": self.service_key,
                "type": "json",
                "sCntrtCntgDates": since.strftime("%Y%m%d"),
                "eCntrtCntgDates": until.strftime("%Y%m%d"),
                "pageNo": page,
                "numOfRows": PAGE_SIZE,
            })
            rows, total = self._extract_rows(payload)
            if page == 1:
                logger.info("[EX] 계약 %s~%s count=%d", since, until, total)
            yield from rows
            fetched += len(rows)
            if fetched >= total or not rows:
                break
            if page >= _MAX_PAGES:
                logger.warning("[EX] max_pages=%d 도달, 중단 (total=%d fetched=%d)",
                               _MAX_PAGES, total, fetched)
                break
            page += 1

    def fetch_notices(self, since: date, until: date) -> Iterator[dict]:
        """입찰공고 — 본 API 미제공. ebid.ex.co.kr 포털 XHR로 후속 구현 예정."""
        logger.info("[EX] 입찰공고는 본 API 범위 밖 — ebid 포털 XHR 후속 과제")
        return iter(())

    def fetch_awards(self, since: date, until: date) -> Iterator[dict]:
        """낙찰 — 미제공. 계약업체(cntrtCrprNm)가 낙찰자 역할."""
        return iter(())

    # ScraperBase 골격 미사용 (OpenAPI 직접 페이징)
    def fetch_list_pages(self, since: date, until: date):  # pragma: no cover
        return iter(())

    def parse_rows(self, page_payload):  # pragma: no cover
        return []

    # ── 파싱 ──────────────────────────────────────────────────────────────

    def _extract_rows(self, payload) -> tuple:
        """응답에서 (목록, 전체건수) 추출. 목록 키 이름이 미확정이라 방어적 탐색."""
        if isinstance(payload, list):
            return payload, len(payload)
        if not isinstance(payload, dict):
            raise RuntimeError(f"[EX] 예상 밖 응답 타입: {type(payload).__name__}")
        rows = None
        for key in ("list", "items", "data", "rows", "resultList"):
            v = payload.get(key)
            if isinstance(v, list):
                rows = v
                break
        if rows is None:
            # dict 값 중 dict 리스트를 탐색 (키 이름 미상 대비)
            for v in payload.values():
                if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                    rows = v
                    break
        if rows is None:
            code = payload.get("code")
            msg = payload.get("message")
            if code is not None and str(code).upper() not in ("SUCCESS", "00", "0", "OK"):
                raise RuntimeError(f"[EX] API 오류 응답 code={code} message={msg}")
            rows = []
        total = self._to_int(payload.get("count"))
        return rows, total if total is not None else len(rows)

    # ── normalize ─────────────────────────────────────────────────────────

    def is_large_construction_contract(self, raw: dict) -> bool:
        """공사(CT) + 계약금액 100억↑ (raw 사전 필터, run_monthly 연동)."""
        code = self._clean(raw.get("pbanClssCd")) or ""
        name = self._clean(raw.get("pbanClssNm")) or ""
        if code not in _CONSTRUCTION_CODES and "공사" not in name:
            return False
        amt = self._to_int(raw.get("cntrtAmt"))
        return amt is not None and amt >= 10_000_000_000

    def normalize_contract(self, raw: dict) -> dict:
        """계약 중심 스키마 변환 (g2b_opnstd.normalize_contract 패턴)."""
        return {
            "source":           self.source,
            "notice_no":        self._clean(raw.get("scbdPbanNo")),
            "contract_no":      self._clean(raw.get("scbdPbanNo")),
            "contract_name":    self._clean(raw.get("cntrtNm")),
            "bsns_div":         self._bsns_div(raw),
            "contract_price":   self._to_int(raw.get("cntrtAmt")),
            "contracted_at":    self._parse_dt(raw.get("cntrtCntgDates")),
            "contract_method":  self._clean(raw.get("cmpttMthd")),
            "is_long_term":     "장기계속" if detect_long_term_from_raw(raw, _LT_KEYS) else None,
            "demand_inst":      "한국도로공사",
            "contract_inst":    self._clean(raw.get("cntrtDptnm")),
            "supervising_dept": self._clean(raw.get("sprvDptnm")),
            "contractor_name":  self._clean(raw.get("cntrtCrprNm")),
            "contractor_bizno": self._clean(raw.get("crno")),
            "raw_payload":      raw,
        }

    def normalize(self, raw: dict) -> dict:
        """공통 공고 스키마 변환 — 계약 raw 기준 (공고 API 부재로 참조용)."""
        notice_no = self._clean(raw.get("scbdPbanNo"))
        return {
            "notice_id":               f"ex:{notice_no}:1",
            "source":                  self.source,
            "notice_no":               notice_no,
            "notice_rev":              1,
            "agency_code":             "EX",
            "title":                   self._clean(raw.get("cntrtNm")),
            "work_type":               self._bsns_div(raw) or "미분류",
            "construction_type":       None,
            "is_long_term_continuing": detect_long_term_from_raw(raw, _LT_KEYS),
            "bid_method":              self._clean(raw.get("cmpttMthd")),
            "estimated_price":         None,   # 계약 API라 추정가격 미제공
            "vat_included":            False,
            "posted_at":               None,
            "bid_open_at":             None,
            "status":                  "계약체결",
            "raw_payload":             raw,
            "source_hash":             self._hash(raw),
            "collected_at":            None,
        }

    # ── helpers ───────────────────────────────────────────────────────────

    def _bsns_div(self, raw: dict) -> Optional[str]:
        name = self._clean(raw.get("pbanClssNm"))
        if name:
            return name
        code = self._clean(raw.get("pbanClssCd"))
        return "공사" if code in _CONSTRUCTION_CODES else code

    def health_check(self) -> bool:
        try:
            today = date.today()
            next(self.fetch_contracts(today, today), None)
            return True
        except Exception:
            return False
