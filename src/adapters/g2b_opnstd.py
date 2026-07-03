"""나라장터 공공데이터개방표준서비스 어댑터 (PubDataOpnStdService v1.2).

OpenAPI 참고자료 기반:
  - Base URL : https://apis.data.go.kr/1230000/ao/PubDataOpnStdService
  - 입찰공고 : getDataSetOpnStdBidPblancInfo  (조회범위: 1개월)
  - 낙찰정보 : getDataSetOpnStdScsbidInfo     (조회범위: 1주일)
  - 계약정보 : getDataSetOpnStdCntrctInfo     (조회범위: 1주일)

※ g2b.py(BidPublicInfoService) 대체 어댑터.
  계약 API가 완비되어 있어 fetch_contracts()가 실제 동작함.
"""
import hashlib
import logging
import os
import time
from datetime import date, timedelta
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import unquote

import requests

from .base import BaseProcurementAdapter
from ..http_client import get_with_retry
from ..long_term_detector import detect_long_term_from_raw

logger = logging.getLogger(__name__)

# 무한/폭주 순회 방지용 페이지 상한 (999건×2000 = 약 200만 건). 실제로는
# totalCount 기준으로 훨씬 먼저 종료된다. 상한 도달 시 경고 로그를 남긴다.
_MAX_PAGES = 2000

_BASE_URL = "https://apis.data.go.kr/1230000/ao/PubDataOpnStdService"
_NOTICE_OP   = "getDataSetOpnStdBidPblancInfo"   # 입찰공고 (1개월 제한)
_AWARD_OP    = "getDataSetOpnStdScsbidInfo"       # 낙찰정보 (1주일 제한)
_CONTRACT_OP = "getDataSetOpnStdCntrctInfo"       # 계약정보 (1주일 제한)

# 낙찰 조회 시 업무구분코드: 3=공사
_BSNS_DIV_CONSTRUCTION = "3"

# 장기계속 판별 필드
_LT_KEYS = ["cntrctCnclsMthdNm", "bidNtceNm", "lngTmCntrctYn"]

# 1주일 제한이 걸린 오퍼레이션의 최대 조회 일수
_WEEKLY_LIMIT_DAYS = 7


class G2BOpnStdAdapter(BaseProcurementAdapter):
    """PubDataOpnStdService 기반 어댑터.

    g2b.py(BidPublicInfoService)의 대체 어댑터로,
    입찰공고·낙찰·계약 세 오퍼레이션이 모두 실제 동작한다.
    """
    source = "g2b_opnstd"
    agency_codes = ["G2B"]

    def __init__(self, api_key=None, timeout=30, rate_limit=0.2):
        # type: (Optional[str], int, float) -> None
        raw_key = api_key or os.getenv("G2B_API_KEY")
        if not raw_key:
            raise ValueError("G2B_API_KEY 환경변수가 없습니다.")
        # data.go.kr 키는 Encoding/Decoding 두 형태가 있다. 이미 URL 인코딩된
        # 키(%2B, %2F 포함)를 그대로 requests에 넘기면 이중 인코딩(%→%25)되어
        # 게이트웨이가 403을 반환한다. 한 번 unquote 해두면 어느 형태든
        # 최종적으로 requests가 올바르게 단일 인코딩한다.
        self.api_key = unquote(raw_key)
        self.timeout = timeout
        self.rate_limit = rate_limit
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    # 내부 유틸                                                             #
    # ------------------------------------------------------------------ #

    def _request(self, operation, params, max_pages=_MAX_PAGES):
        # type: (str, dict, int) -> Iterator[Dict]
        """단일 날짜 범위로 API 호출 → items를 페이지 단위로 yield.

        제너레이터라 호출부에서 스트리밍 필터가 가능하고 전체 결과를 메모리에
        쌓지 않는다. totalCount/진행 상황을 로그로 남겨 대량 수집을 관측 가능하게
        하고, max_pages 안전장치로 폭주를 막는다.
        """
        url = "{}/{}".format(_BASE_URL, operation)
        page_no = 1
        fetched = 0

        while True:
            query = {
                "serviceKey": self.api_key,
                "type": "json",
                "numOfRows": 999,
                "pageNo": page_no,
            }
            query.update(params)
            resp = get_with_retry(
                url, query, timeout=self.timeout, session=self.session, label="G2B",
            )
            data = resp.json()

            body = data.get("response", {}).get("body", {})
            total_count = int(body.get("totalCount", 0) or 0)
            items = body.get("items", [])

            if isinstance(items, dict):
                items = items.get("item", [])
            if isinstance(items, dict):
                items = [items]
            items = items or []

            if page_no == 1 and total_count > 5000:
                logger.info("[G2B] %s totalCount=%d — 대량 수집 시작", operation, total_count)

            for item in items:
                yield item
            fetched += len(items)

            if fetched >= total_count or not items:
                break
            if page_no >= max_pages:
                logger.warning("[G2B] %s max_pages=%d 도달, 중단 (total=%d fetched=%d)",
                               operation, max_pages, total_count, fetched)
                break
            page_no += 1
            if page_no % 50 == 0:
                logger.info("[G2B] %s 진행 page=%d fetched=%d/%d",
                            operation, page_no, fetched, total_count)
            time.sleep(self.rate_limit)

    def _request_weekly_chunks(self, operation, since, until, extra_params):
        # type: (str, date, date, dict) -> Iterator[Dict]
        """조회범위 1주일 제한 오퍼레이션 대응: 7일 단위로 분할 호출."""
        cursor = since
        while cursor <= until:
            chunk_end = min(cursor + timedelta(days=_WEEKLY_LIMIT_DAYS - 1), until)
            params = {
                "cntrctCnclsBgnDate": cursor.strftime("%Y%m%d"),
                "cntrctCnclsEndDate": chunk_end.strftime("%Y%m%d"),
            }
            params.update(extra_params)
            for item in self._request(operation, params):
                yield item
            cursor = chunk_end + timedelta(days=1)

    def _request_weekly_chunks_award(self, since, until):
        # type: (date, date) -> Iterator[Dict]
        """낙찰정보 전용 분할 호출 (개찰일시 기준, bsnsDivCd=공사 고정)."""
        cursor = since
        while cursor <= until:
            chunk_end = min(cursor + timedelta(days=_WEEKLY_LIMIT_DAYS - 1), until)
            params = {
                "bsnsDivCd": _BSNS_DIV_CONSTRUCTION,
                "opengBgnDt": cursor.strftime("%Y%m%d") + "0000",
                "opengEndDt": chunk_end.strftime("%Y%m%d") + "2359",
            }
            for item in self._request(_AWARD_OP, params):
                yield item
            cursor = chunk_end + timedelta(days=1)

    def _to_int(self, value):
        # type: (object) -> Optional[int]
        if value in (None, "", "-"):
            return None
        try:
            return int(float(str(value).replace(",", "").replace("원", "").strip()))
        except (ValueError, TypeError):
            return None

    def _estimated_price_vat_excl(self, raw):
        # type: (dict) -> Tuple[Optional[int], bool]
        """추정가격(VAT 제외) 반환. VAT 포함 표기이면 /1.1 환산."""
        vat_included = False
        for key in ["presmptPrce", "asignBdgtAmt", "bssAmt"]:
            amt = self._to_int(raw.get(key))
            if amt:
                if raw.get("vatIncldYn") == "Y":
                    vat_included = True
                    amt = int(amt / 1.1)
                return amt, vat_included
        return None, vat_included

    def _is_construction(self, raw):
        # type: (dict) -> bool
        value = " ".join(str(raw.get(k, "")) for k in ["bsnsDivNm", "bidNtceNm"])
        return "공사" in value

    def _construction_type(self, raw):
        # type: (dict) -> Optional[str]
        text = " ".join(str(raw.get(k, "")) for k in ["indstrytyLmtYn", "bidprcPsblIndstrytyNm", "bidNtceNm"])
        if "전문" in text:
            return "전문"
        if "종합" in text:
            return "종합"
        return None

    # ------------------------------------------------------------------ #
    # 공개 인터페이스                                                        #
    # ------------------------------------------------------------------ #

    def fetch_notices(self, since, until):
        # type: (date, date) -> Iterator[Dict]
        """입찰공고 수집 (입찰공고일시 기준, 1개월 제한 → 월 단위 호출 가능)."""
        params = {
            "bidNtceBgnDt": since.strftime("%Y%m%d") + "0000",
            "bidNtceEndDt": until.strftime("%Y%m%d") + "2359",
        }
        for item in self._request(_NOTICE_OP, params):
            yield item

    def fetch_awards(self, since, until):
        # type: (date, date) -> Iterator[Dict]
        """낙찰정보 수집 (개찰일시 기준, 1주일 단위 자동 분할)."""
        for item in self._request_weekly_chunks_award(since, until):
            yield item

    def fetch_contracts(self, since, until):
        # type: (date, date) -> Iterator[Dict]
        """계약정보 수집 (계약체결일자 기준, 1주일 단위 자동 분할).

        g2b.py에서 placeholder였던 부분이 실제 동작하는 구현으로 완성됨.
        insttDivCd/insttCd 미지정 시 전체 기관 조회.
        """
        for item in self._request_weekly_chunks(_CONTRACT_OP, since, until, {}):
            yield item

    def normalize(self, raw):
        # type: (dict) -> dict
        estimated_price, vat_included = self._estimated_price_vat_excl(raw)
        notice_no  = raw.get("bidNtceNo") or raw.get("ntceNo")
        notice_rev = self._to_int(raw.get("bidNtceOrd")) or 0
        payload_hash = hashlib.sha256(
            str(sorted(raw.items())).encode()
        ).hexdigest()

        return {
            "notice_id":               "g2b_opnstd:{}:{}".format(notice_no, notice_rev),
            "source":                  self.source,
            "notice_no":               notice_no,
            "notice_rev":              notice_rev,
            "agency_code":             "G2B",
            "title":                   raw.get("bidNtceNm"),
            "work_type":               "공사" if self._is_construction(raw) else raw.get("bsnsDivNm"),
            "construction_type":       self._construction_type(raw),
            "is_long_term_continuing": detect_long_term_from_raw(raw, _LT_KEYS),
            "bid_method":              raw.get("cntrctCnclsMthdNm") or raw.get("bidwinrDcsnMthdNm"),
            "estimated_price":         estimated_price,
            "vat_included":            vat_included,
            "posted_at":               raw.get("bidNtceDate"),
            "bid_open_at":             raw.get("opengDate") or raw.get("bidClseDate"),
            "status":                  raw.get("bidNtceSttusNm") or raw.get("cntrctCnclsSttusNm") or "공고중",
            "raw_payload":             raw,
            "source_hash":             payload_hash,
            "collected_at":            None,
            "_award_corp":             raw.get("fnlSucsfCorpNm"),
            "_award_corp_bizrno":      raw.get("fnlSucsfCorpBizrno"),
            "_award_amt":              self._to_int(raw.get("fnlSucsfAmt")),
            "_award_rate":             raw.get("fnlSucsfRt"),
            "_contract_amt":           self._to_int(raw.get("cntrctAmt")),
            "_contract_date":          raw.get("cntrctCnclsDate"),
            "_demand_inst":            raw.get("dmndInsttNm"),
        }

    def health_check(self):
        # type: () -> bool
        try:
            # 첫 페이지만 확인 (max_pages=1). 예외 없이 순회되면 정상.
            for _ in self._request(
                _NOTICE_OP,
                {"bidNtceBgnDt": "202601010000", "bidNtceEndDt": "202601072359"},
                max_pages=1,
            ):
                break
            return True
        except Exception:
            return False
