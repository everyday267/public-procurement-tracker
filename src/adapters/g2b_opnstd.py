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
from typing import Callable, Dict, Iterator, List, Optional, Tuple
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

# bidNtceNo 서버측 필터 지원 여부 판별 임계값. 특정 공고번호로 조회했을 때
# totalCount가 이보다 크면 필터가 무시된 것(전국 반환)으로 보고 폴백한다.
_SCOPE_PROBE_MAX = 500

# 계약 규모 필터: 계약금액 100억 이상. (공사이행보증서 대상 계약 규모 파악)
CONTRACT_MIN_PRICE = 10_000_000_000


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
                max_retries=6, backoff_base=3.0,
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

    def _monthly_chunks(self, since, until):
        # type: (date, date) -> Iterator[Tuple[date, date]]
        """공고 오퍼레이션의 1개월 조회 제한 대응: 달력 월 단위로 분할.

        한 달 이내 범위(월간 수집)는 그대로 1회 호출이 되고, 분기·연간 등
        여러 달 범위는 달 경계로 쪼개 순차 호출한다(동시 실행 없이 한 런에서
        직렬 처리 → data.go.kr 동시요청 502 회피)."""
        cursor = since
        while cursor <= until:
            if cursor.month == 12:
                month_end = date(cursor.year, 12, 31)
            else:
                month_end = date(cursor.year, cursor.month + 1, 1) - timedelta(days=1)
            chunk_end = min(month_end, until)
            yield cursor, chunk_end
            cursor = chunk_end + timedelta(days=1)

    def fetch_notices(self, since, until):
        # type: (date, date) -> Iterator[Dict]
        """입찰공고 수집 (입찰공고일시 기준, 1개월 제한 → 월 단위 자동 분할)."""
        for begin, end in self._monthly_chunks(since, until):
            params = {
                "bidNtceBgnDt": begin.strftime("%Y%m%d") + "0000",
                "bidNtceEndDt": end.strftime("%Y%m%d") + "2359",
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

    # ── 대상 공고번호(bidNtceNo) 기반 스코프 조회 ──────────────────────────
    #
    # 개방표준 계약/낙찰 API는 서버측 기관·공사 필터가 없어 전국을 전부
    # 순회해야 했다(월 수십분). 우리가 필요한 건 필터된 공고(공사 100억↑)에
    # 매칭되는 건뿐이므로, bidNtceNo로 좁혀 조회한다. 단 이 파라미터가
    # 서버측에서 실제로 적용되는지 불확실하므로 probe로 검증하고, 미지원이면
    # 기존 전국 스윕으로 안전하게 폴백한다.

    def _weekly_windows(self, since, until):
        # type: (date, date) -> Iterator[Tuple[date, date]]
        cursor = since
        while cursor <= until:
            end = min(cursor + timedelta(days=_WEEKLY_LIMIT_DAYS - 1), until)
            yield cursor, end
            cursor = end + timedelta(days=1)

    def _contract_params(self, ws, we):
        # type: (date, date) -> dict
        return {
            "cntrctCnclsBgnDate": ws.strftime("%Y%m%d"),
            "cntrctCnclsEndDate": we.strftime("%Y%m%d"),
        }

    def _award_params(self, ws, we):
        # type: (date, date) -> dict
        return {
            "bsnsDivCd": _BSNS_DIV_CONSTRUCTION,
            "opengBgnDt": ws.strftime("%Y%m%d") + "0000",
            "opengEndDt": we.strftime("%Y%m%d") + "2359",
        }

    def _total_count(self, operation, params):
        # type: (str, dict) -> Optional[int]
        """numOfRows=1로 totalCount만 조회. 실패 시 None."""
        url = "{}/{}".format(_BASE_URL, operation)
        query = {"serviceKey": self.api_key, "type": "json", "numOfRows": 1, "pageNo": 1}
        query.update(params)
        try:
            resp = get_with_retry(url, query, timeout=self.timeout,
                                  session=self.session, label="G2B")
            body = resp.json().get("response", {}).get("body", {})
            return int(body.get("totalCount", 0) or 0)
        except Exception:
            return None

    def _fetch_scoped_or_sweep(self, operation, notice_nos, since, until, param_builder):
        # type: (str, set, date, date, Callable) -> Iterator[Dict]
        """bidNtceNo 스코프 조회. 서버측 필터 미지원이면 전국 스윕 폴백."""
        if not notice_nos:
            return  # 대상 공고 없음 → 조회 불필요
        windows = list(self._weekly_windows(since, until))
        sample = next(iter(notice_nos))
        probe = self._total_count(operation, dict(param_builder(*windows[0]), bidNtceNo=sample))
        if probe is not None and probe <= _SCOPE_PROBE_MAX:
            logger.info("[G2B] %s: bidNtceNo 서버측 필터 사용 (공고 %d개 × 주 %d개)",
                        operation, len(notice_nos), len(windows))
            for no in notice_nos:
                for ws, we in windows:
                    params = dict(param_builder(ws, we), bidNtceNo=no)
                    for item in self._request(operation, params, max_pages=5):
                        yield item
        else:
            logger.warning("[G2B] %s: bidNtceNo 필터 미지원(probe=%s) → 전국 스윕 폴백",
                           operation, probe)
            for ws, we in windows:
                for item in self._request(operation, param_builder(ws, we)):
                    yield item

    def fetch_contracts_scoped(self, notice_nos, since, until):
        # type: (set, date, date) -> Iterator[Dict]
        """필터된 공고번호에 대한 계약만 조회 (전국 순회 회피)."""
        for item in self._fetch_scoped_or_sweep(
                _CONTRACT_OP, notice_nos, since, until, self._contract_params):
            yield item

    def fetch_awards_scoped(self, notice_nos, since, until):
        # type: (set, date, date) -> Iterator[Dict]
        """필터된 공고번호에 대한 낙찰만 조회 (전국 순회 회피)."""
        for item in self._fetch_scoped_or_sweep(
                _AWARD_OP, notice_nos, since, until, self._award_params):
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

    def normalize_contract(self, raw):
        # type: (dict) -> dict
        """계약 레코드를 계약 중심 스키마로 변환 (체결일 기준 독립 수집용).

        getDataSetOpnStdCntrctInfo 실제 필드 기반. 공고 매칭 없이 계약 자체가
        1급 데이터가 된다. rprsntCorpNm(계약상대자)이 사실상 낙찰자 역할.
        """
        return {
            "source":               self.source,
            "notice_no":            raw.get("bidNtceNo"),
            "contract_no":          raw.get("cntrctNo"),
            "unity_contract_no":    raw.get("untyCntrctNo"),
            "contract_name":        raw.get("cntrctNm") or raw.get("bidNtceNm"),
            "bsns_div":             raw.get("bsnsDivNm"),
            "contract_price":       self._to_int(raw.get("cntrctAmt")),
            "total_contract_price": self._to_int(raw.get("ttalCntrctAmt")),
            "contracted_at":        raw.get("cntrctCnclsDate"),
            "contract_method":      raw.get("cntrctCnclsMthdNm"),
            "contract_status":      raw.get("cntrctCnclsSttusNm"),
            "is_long_term":         raw.get("lngtrmCtnuDivNm"),
            "demand_inst":          raw.get("dmndInsttNm"),
            "contract_inst":        raw.get("cntrctInsttNm"),
            "contractor_name":      raw.get("rprsntCorpNm"),
            "contractor_bizno":     raw.get("rprsntCorpBizrno"),
            "contract_period":      raw.get("cntrctPrd"),
            "raw_payload":          raw,
        }

    def is_large_construction_contract(self, raw):
        # type: (dict) -> bool
        """공사 + 100억↑ 계약인지 (raw 레코드 기준, 빠른 사전 필터).

        장기계속공사의 차수(연차) 계약은 cntrctAmt(이번 차수 금액)가 100억
        미만이어도 ttalCntrctAmt(총계약금액)가 100억↑이면 대상이다 — 공사이행
        보증 관점의 '주계약'은 총액 기준. 기존 `cntrctAmt or ttal` 구현은
        cntrctAmt가 있으면 총액을 아예 안 봐서 연차계약을 전부 누락시켰다.
        """
        if "공사" not in str(raw.get("bsnsDivNm", "")):
            return False
        amounts = [a for a in (self._to_int(raw.get("cntrctAmt")),
                               self._to_int(raw.get("ttalCntrctAmt"))) if a is not None]
        return bool(amounts) and max(amounts) >= CONTRACT_MIN_PRICE

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
