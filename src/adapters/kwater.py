"""kwater.py — 한국수자원공사(K-water) 어댑터 (Phase 2 Wave A)

수집 경로 (2026-07-04 3차 조사 run #19에서 XHR 캡처로 확정):
  ebid.kwater.or.kr (WebSquare5 SPA)의 비로그인 입찰공고 목록 XHR
  - POST https://ebid.kwater.or.kr/bidpblanc/bidpblancsttus/retrievePaginatedBidPblancList.do
  - 요청: {"dmaSearchData": {"cntrctDivNm", "recordCountPerPage",
           "tndrPblancStartDe", "tndrPblancEndDe", ...},
           "ktagTokenField": "BID_savedToken", "BID_savedToken": null}
  - 응답: {"message": {"code": "success"}, "data": {"pagination": {...,
           "totalCount"}, "list": [{tndrPblancDe, tndrPbanno, tndrPblancNm,
           ctrmthdCdNm, tndrStat, cntrctDivNm, ...}]}}

실서비스 스키마 (2026-07-04 run #20 [schema] 로그로 확정, 11개 필드):
  cntrctDeptNm(계약부서)·cntrctDivNm(계약구분)·ctrmthdCdNm(계약방법)·
  rqestAmt(요청금액)·tndrPartcptEntrpsCo·tndrPbanno(공고번호)·
  tndrPblancDe(공고일)·tndrPblancEnddt(마감일시)·tndrPblancNm(공고명)·
  tndrPrqudoCo·tndrStat(진행상태)
※ rqestAmt의 추정가격/기초금액 여부·VAT 포함 여부는 실서비스 표본 대조로
  확인 필요 (⚠️ 사용자, ebid.kwater.or.kr 화면 대조).

낙찰·계약: 목록 XHR 미확보 → 빈 결과(ScraperBase 기본), G2B 보완.
페이지네이션 파라미터(pageIndex/firstIndex/lastIndex)는 사이트 내 다른 API
(retrieveNewNoticeList의 firstIndex/lastIndex) 패턴을 함께 전달해 방어한다.
"""
import logging
from datetime import date
from typing import Iterable, Iterator, Optional

from .scraper_base import ScraperBaseAdapter
from ..long_term_detector import detect_long_term_from_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://ebid.kwater.or.kr"
LIST_OP = "/bidpblanc/bidpblancsttus/retrievePaginatedBidPblancList.do"
PAGE_SIZE = 100
_MAX_PAGES = 500

# 금액 필드 — rqestAmt(요청금액)로 확정 (run #20 실서비스 스키마).
# 뒤 항목들은 스키마 변경 대비 폴백.
_PRICE_KEYS = ["rqestAmt", "presmtPc", "presmtPrc", "bdgtAmt"]
_VAT_KEYS = ["vatYn", "vatIncldYn"]

# 장기계속 판별 대상 필드
_LT_KEYS = ["tndrPblancNm", "ctrmthdCdNm"]


class KWaterAdapter(ScraperBaseAdapter):
    """K-water 전자조달(ebid.kwater.or.kr) 입찰공고 XHR 어댑터. 인증키 불필요."""

    source = "kwater"
    agency_codes = ["KWATER"]

    # ── fetch ─────────────────────────────────────────────────────────────

    def fetch_list_pages(self, since: date, until: date) -> Iterator[dict]:
        page = 1
        fetched = 0
        while True:
            payload = {
                "dmaSearchData": {
                    "cntrctDivNm": "",  # 전체 조회 후 클라이언트측 공사 필터
                    "recordCountPerPage": PAGE_SIZE,
                    "pageIndex": page,
                    "firstIndex": (page - 1) * PAGE_SIZE + 1,
                    "lastIndex": page * PAGE_SIZE,
                    "tndrPblancStartDe": since.strftime("%Y%m%d"),
                    "tndrPblancEndDe": until.strftime("%Y%m%d"),
                },
                "ktagTokenField": "BID_savedToken",
                "BID_savedToken": None,
            }
            resp = self.post_json(BASE_URL + LIST_OP, payload)
            code = resp.get("message", {}).get("code")
            if code != "success":
                raise RuntimeError(f"[kwater] 목록 XHR 오류 응답: {str(resp)[:200]}")
            data = resp.get("data", {})
            rows = data.get("list", []) or []
            total = int(data.get("pagination", {}).get("totalCount",
                        data.get("count", len(rows))) or 0)
            if page == 1:
                logger.info("[KWATER] 공고 %s~%s totalCount=%d",
                            since, until, total)
            yield data
            fetched += len(rows)
            if fetched >= total or not rows:
                break
            if page >= _MAX_PAGES:
                logger.warning("[KWATER] max_pages=%d 도달, 중단 (total=%d fetched=%d)",
                               _MAX_PAGES, total, fetched)
                break
            page += 1

    def parse_rows(self, page_payload: dict) -> Iterable[dict]:
        return page_payload.get("list", []) or []

    # ── normalize ─────────────────────────────────────────────────────────

    def normalize(self, raw: dict) -> dict:
        notice_no = self._clean(raw.get("tndrPbanno"))
        estimated_price, vat_included = self._price(raw)
        return {
            "notice_id":               f"kwater:{notice_no}:1",
            "source":                  self.source,
            "notice_no":               notice_no,
            "notice_rev":              1,
            "agency_code":             "KWATER",
            "title":                   self._clean(raw.get("tndrPblancNm")),
            "work_type":               self._work_type(raw),
            "construction_type":       self._construction_type(raw),
            "is_long_term_continuing": detect_long_term_from_raw(raw, _LT_KEYS),
            "bid_method":              self._clean(raw.get("ctrmthdCdNm")),
            "estimated_price":         estimated_price,
            "vat_included":            vat_included,
            "posted_at":               self._parse_dt(raw.get("tndrPblancDe")),
            "bid_open_at":             self._parse_dt(raw.get("tndrPblancEnddt")),
            "status":                  self._clean(raw.get("tndrStat")) or "공고중",
            "raw_payload":             raw,
            "source_hash":             self._hash(raw),
            "collected_at":            None,
        }

    # ── helpers ───────────────────────────────────────────────────────────

    def _price(self, raw: dict):
        for key in _PRICE_KEYS:
            amt = self._to_int(raw.get(key))
            if amt is not None:
                vat = any(str(raw.get(k, "")).upper() == "Y" for k in _VAT_KEYS)
                return (int(amt / 1.1), True) if vat else (amt, False)
        return None, False

    def _work_type(self, raw: dict) -> str:
        div = self._clean(raw.get("cntrctDivNm")) or ""
        if div:
            return "공사" if "공사" in div else div
        title = self._clean(raw.get("tndrPblancNm")) or ""
        return "공사" if "공사" in title else "미분류"

    def _construction_type(self, raw: dict) -> Optional[str]:
        text = " ".join(str(raw.get(k, "")) for k in
                        ["cntrctDivNm", "tndrPblancNm"])
        if "전문" in text:
            return "전문"
        if "종합" in text:
            return "종합"
        return None

    def health_check(self) -> bool:
        try:
            today = date.today()
            next(iter(self.fetch_list_pages(today, today)))
            return True
        except StopIteration:
            return True
        except Exception:
            return False
