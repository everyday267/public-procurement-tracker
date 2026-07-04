"""kogas.py — 한국가스공사(KOGAS) 어댑터 (Phase 2 Wave A)

수집 경로 (2026-07-04 5~8차 조사로 확정 — docs/PHASE2_WAVE_A_RESEARCH.md):
  bid.kogas.or.kr:9443 전자조달(구형 JSP, euc-kr) 비로그인 스크래핑
  - 목록: GET /supplier/contents/bid/bid_list_notice_frm.jsp
      파라미터 page(1~), worktype(C=공사/S=용역/M·F=물품), title,
      e_startday/e_endday(마감일), o_startday/o_endday(개찰일), reqbidno
      페이지당 15행, "Total Records : N ... Pages :p/T" 표기
      행: viewBid('공고번호','차수','회차','유형') — 공고번호 앞 8자리=공고일
      유형: B=전자입찰, E=가격조사(전자견적), S=매각
  - 상세: POST /supplier/contents/bid/bid_detail_view_notice.jsp
      form(notice_code, bid_code, round, is_gongo=true, ...)
      가격정보 표에 추정가격(부가세 별도)·부가세·합계금액,
      입찰진행순서 표에 공고일시·마감일시·개찰일시

수집 전략:
  - worktype=C(공사)로 목록 전 페이지 순회, 공고번호 앞 8자리로 기간 필터
    (목록 정렬이 마감일순이라 조기 중단 없이 전량 순회)
  - 기간 내 공사 공고만 상세 POST로 추정가격 보강 (월 수십 건 수준)
  - 낙찰·계약: 별도 화면(개찰결과) 후속 과제 → G2B 보완 (ScraperBase 기본)
"""
import logging
import re
from datetime import date
from typing import Iterable, Iterator, Optional

from .scraper_base import ScraperBaseAdapter
from ..http_client import post_with_retry
from ..long_term_detector import detect_long_term

logger = logging.getLogger(__name__)

BASE_URL = "https://bid.kogas.or.kr:9443"
LIST_PATH = "/supplier/contents/bid/bid_list_notice_frm.jsp"
DETAIL_PATH = "/supplier/contents/bid/bid_detail_view_notice.jsp"
PAGE_ROWS = 15
_MAX_PAGES = 200

_BID_TYPE = {"B": "전자입찰", "E": "가격조사", "S": "매각"}

# 목록 데이터 행: viewBid('공고','차수','회차','유형') ... 셀들
_ROW_RE = re.compile(
    r"viewBid\('(?P<nc>\d+)','(?P<bc>\d+)','(?P<rd>\d+)','(?P<tp>\w)'\)")
_CELL_RE = re.compile(r'<td class="c_c">\s*(?:<[^>]+>)*([^<]*)', re.I)
_TITLE_RE = re.compile(r"<span[^>]*title=\"([^\"]+)\"")
_TOTAL_RE = re.compile(r"Total Records\s*:\s*(\d+)")

# 상세: "라벨</td> <td ...>값" 패턴
def _detail_field(html: str, label: str) -> Optional[str]:
    m = re.search(label + r"</td>\s*<td[^>]*>\s*(?:<[^>]+>\s*)*([^<]+)", html)
    return m.group(1).strip() if m else None


class KOGASAdapter(ScraperBaseAdapter):
    """한국가스공사 전자조달(bid.kogas.or.kr:9443) 스크래핑 어댑터. 키 불필요."""

    source = "kogas"
    agency_codes = ["KOGAS"]
    request_interval = 2.0  # 구형 서버 — 간헐 커넥션 거부 관측, 간격 준수

    def __init__(self, timeout: int = 30, fetch_detail: bool = True):
        super().__init__(timeout=timeout)
        self.fetch_detail = fetch_detail
        self._warmed = False

    # ── HTTP (euc-kr 대응) ────────────────────────────────────────────────

    def _get_html(self, path: str, params: Optional[dict] = None) -> str:
        if not self._warmed:
            # 세션 쿠키 확보 (첫 접근)
            self._warmed = True
            try:
                self._get(BASE_URL + "/supplier/index.jsp")
            except Exception:
                pass
        r = self._get(BASE_URL + path, params)
        r.encoding = "euc-kr"
        return r.text

    def _post_html(self, path: str, data: dict) -> str:
        r = post_with_retry(
            BASE_URL + path, data=data, timeout=self.timeout, session=self.session,
            headers=self._headers(), max_retries=4,
            sleep_before=self.request_interval, label=self.source,
        )
        r.encoding = "euc-kr"
        return r.text

    # ── fetch ─────────────────────────────────────────────────────────────

    def fetch_list_pages(self, since: date, until: date) -> Iterator[str]:
        """공사(worktype=C) 목록 전 페이지 HTML을 순서대로 yield."""
        page = 1
        total_pages = None
        while True:
            html = self._get_html(LIST_PATH, {"page": page, "worktype": "C"})
            if total_pages is None:
                m = _TOTAL_RE.search(html)
                total = int(m.group(1)) if m else 0
                total_pages = max(1, -(-total // PAGE_ROWS))
                logger.info("[KOGAS] 공사 공고 Total=%d (%d페이지)", total, total_pages)
            yield html
            if page >= min(total_pages, _MAX_PAGES):
                break
            page += 1

    def parse_rows(self, page_payload: str) -> Iterable[dict]:
        """목록 HTML → 행 dict. 데이터 행은 'tr onmouseout' 블록."""
        for block in page_payload.split("<tr onmouseout")[1:]:
            block = block.split("</tr>")[0]
            m = _ROW_RE.search(block)
            if not m:
                continue
            cells = [c.strip() for c in _CELL_RE.findall(block)]
            tm = _TITLE_RE.search(block)
            # 셀 순서: 입찰번호, (입찰명은 class="c"), 입찰구분, 업무구분,
            #          계약방법, 마감일시, 개찰일시, 취소여부
            texts = [c for c in cells[1:] if c]  # cells[0]=입찰번호 링크텍스트
            yield {
                "notice_code": m.group("nc"),
                "bid_code":    m.group("bc"),
                "round":       m.group("rd"),
                "bid_type":    _BID_TYPE.get(m.group("tp"), m.group("tp")),
                "title":       tm.group(1) if tm else None,
                "bid_kind":    texts[0] if len(texts) > 0 else None,
                "work_div":    texts[1] if len(texts) > 1 else None,
                "method":      texts[2] if len(texts) > 2 else None,
                "close_dt":    texts[3] if len(texts) > 3 else None,
                "open_dt":     texts[4] if len(texts) > 4 else None,
            }

    def fetch_notices(self, since: date, until: date) -> Iterator[dict]:
        """목록 순회 → 공고번호 앞 8자리(공고일) 기간 필터 → 상세 보강."""
        s, u = since.strftime("%Y%m%d"), until.strftime("%Y%m%d")
        for page_html in self.fetch_list_pages(since, until):
            for row in self.parse_rows(page_html):
                posted = row["notice_code"][:8]
                if not (s <= posted <= u):
                    continue
                row["posted_date"] = posted
                if self.fetch_detail:
                    try:
                        row.update(self._fetch_detail(row))
                    except Exception as e:
                        logger.warning("[KOGAS] 상세 조회 실패 %s: %s",
                                       row["notice_code"], e)
                yield row

    def _fetch_detail(self, row: dict) -> dict:
        html = self._post_html(DETAIL_PATH, {
            "notice_code": row["notice_code"], "bid_code": row["bid_code"],
            "round": row["round"], "is_gongo": "true",
            "is_estimate": "false", "is_mine": "false",
        })
        return {
            "estm_price":  _detail_field(html, "추정가격"),
            "vat":         _detail_field(html, "부가세"),
            "total_price": _detail_field(html, "합계금액"),
            "posted_dt":   _detail_field(html, "공고일시"),
            "close_dt_d":  _detail_field(html, "입찰신청및입찰마감일시"),
            "open_dt_d":   _detail_field(html, "개찰일시"),
        }

    # 낙찰·계약: ScraperBase 기본(빈 결과) — G2B 보완

    # ── normalize ─────────────────────────────────────────────────────────

    def normalize(self, raw: dict) -> dict:
        nc, bc = raw.get("notice_code"), raw.get("bid_code") or "001"
        rev = int(raw.get("round") or 1)
        title = self._clean(raw.get("title")) or ""
        return {
            "notice_id":               f"kogas:{nc}-{bc}:{rev}",
            "source":                  self.source,
            "notice_no":               f"{nc}{bc}",
            "notice_rev":              rev,
            "agency_code":             "KOGAS",
            "title":                   title or None,
            "work_type":               self._clean(raw.get("work_div")) or "미분류",
            "construction_type":       self._construction_type(title),
            "is_long_term_continuing": detect_long_term(title),
            "bid_method":              self._clean(raw.get("method")),
            # 상세 가격정보의 추정가격은 부가세 별도 표기 → 환산 불필요
            "estimated_price":         self._to_int(raw.get("estm_price")),
            "vat_included":            False,
            "posted_at":               self._parse_dt(raw.get("posted_dt")
                                                      or raw.get("posted_date")),
            "bid_open_at":             self._parse_dt(raw.get("open_dt_d")
                                                      or raw.get("open_dt")),
            "status":                  self._clean(raw.get("bid_kind")) or "공고중",
            "raw_payload":             raw,
            "source_hash":             self._hash(raw),
            "collected_at":            None,
        }

    def _construction_type(self, text: str) -> Optional[str]:
        if "전문" in text:
            return "전문"
        if "종합" in text:
            return "종합"
        return None

    def health_check(self) -> bool:
        try:
            html = self._get_html(LIST_PATH, {"page": 1, "worktype": "C"})
            return _TOTAL_RE.search(html) is not None
        except Exception:
            return False
