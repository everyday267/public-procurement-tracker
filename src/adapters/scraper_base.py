"""scraper_base.py — 스크래핑 어댑터 공통층 (실행계획 §3.3, M2-0)

Phase 2~3 자체 조달시스템(XHR/HTML) 어댑터의 공통 기반:
  - keep-alive 세션 + User-Agent 로테이션 (일부 WAF의 기본 UA 차단 대응)
  - 요청 간격 ≥ 2초 (스크래핑 예의) + 지수 백오프 재시도
  - JSON/HTML 응답 헬퍼 (get_json / get_text)
  - fetch_notices 골격: fetch_list_pages(목록 XHR 순회) → parse_rows(파싱 훅)

서브클래스는 최소한 fetch_list_pages()·parse_rows()·normalize()를 구현한다.
낙찰·계약 미제공 사이트는 fetch_awards/fetch_contracts 기본 구현(빈 결과)을
그대로 두고 G2B 계약정보로 보완한다 (kepco.py 패턴).
"""
import hashlib
import itertools
import json
import logging
from datetime import date
from typing import Iterable, Iterator, Optional

import requests

from .base import BaseProcurementAdapter
from ..http_client import get_with_retry

logger = logging.getLogger(__name__)

SCRAPE_INTERVAL = 2.0   # 스크래핑 요청 간격 하한 (실행계획 §2.2 srm 폴백 규격 준용)
MAX_RETRIES = 4

# 로테이션용 UA 후보 — 과도한 위장 없이 일반 브라우저 계열 위주
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (compatible; procurement-tracker/1.0)",
]


class ScraperBaseAdapter(BaseProcurementAdapter):
    """XHR/HTML 스크래핑 어댑터 공통 기반."""

    #: 서브클래스에서 지정 — 요청 간격(초). 사이트별로 늘릴 수 있다.
    request_interval: float = SCRAPE_INTERVAL

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self._ua_cycle = itertools.cycle(USER_AGENTS)

    # ── HTTP ──────────────────────────────────────────────────────────────

    def _headers(self, extra: Optional[dict] = None) -> dict:
        return {"User-Agent": next(self._ua_cycle), **(extra or {})}

    def _get(self, url: str, params: Optional[dict] = None,
             headers: Optional[dict] = None) -> requests.Response:
        return get_with_retry(
            url, params or {}, timeout=self.timeout, session=self.session,
            headers=self._headers(headers), max_retries=MAX_RETRIES,
            sleep_before=self.request_interval, label=self.source,
        )

    def get_json(self, url: str, params: Optional[dict] = None,
                 headers: Optional[dict] = None):
        """XHR JSON 응답. JSON이 아니면 RuntimeError (차단/개편 감지)."""
        r = self._get(url, params, headers)
        try:
            return r.json()
        except ValueError:
            raise RuntimeError(
                f"[{self.source}] 비JSON 응답 (차단 또는 개편 의심): {r.text[:200]}")

    def get_text(self, url: str, params: Optional[dict] = None,
                 headers: Optional[dict] = None) -> str:
        """HTML 등 텍스트 응답."""
        return self._get(url, params, headers).text

    # ── fetch 골격 ────────────────────────────────────────────────────────

    def fetch_notices(self, since: date, until: date) -> Iterator[dict]:
        """목록 페이지 순회(fetch_list_pages) → 행 파싱(parse_rows)."""
        for page_payload in self.fetch_list_pages(since, until):
            yield from self.parse_rows(page_payload)

    def fetch_list_pages(self, since: date, until: date) -> Iterator:
        """목록 XHR/HTML 페이지 payload를 순서대로 yield. 서브클래스 구현."""
        raise NotImplementedError

    def parse_rows(self, page_payload) -> Iterable[dict]:
        """페이지 payload → 공고 raw dict 목록. 서브클래스 구현."""
        raise NotImplementedError

    def fetch_awards(self, since: date, until: date) -> Iterator[dict]:
        """기본: 미제공 — G2B 계약정보로 보완 (필요 시 서브클래스 오버라이드)."""
        logger.info("[%s] 낙찰 수집 미제공 — G2B로 보완", self.source)
        return iter(())

    def fetch_contracts(self, since: date, until: date) -> Iterator[dict]:
        """기본: 미제공 — G2B 계약정보로 보완 (필요 시 서브클래스 오버라이드)."""
        logger.info("[%s] 계약 수집 미제공 — G2B로 보완", self.source)
        return iter(())

    # ── 공통 헬퍼 (kepco/lh 패턴 통일) ─────────────────────────────────────

    def _clean(self, v) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s if s not in ("", "-") else None

    def _to_int(self, v) -> Optional[int]:
        v = self._clean(v)
        if v is None:
            return None
        try:
            return int(float(v.replace(",", "").replace("원", "")))
        except (ValueError, TypeError):
            return None

    def _parse_dt(self, v) -> Optional[str]:
        """YYYYMMDD(HHMMSS) 또는 구분자 포함 날짜 → ISO 문자열."""
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
        return hashlib.md5(
            json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()
