import hashlib
import os
import time
from datetime import date
from typing import Iterator

import requests

from .base import BaseProcurementAdapter
from ..long_term_detector import detect_long_term_from_raw

# 나라장터 OpenAPI 엔드포인트
_BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
_NOTICE_PATH = "getBidPblancListInfoCnstwkPPSSrch"   # 공사 공고
_AWARD_PATH  = "getBidResultListInfoCnstwkPPSSrch"   # 낙찰결과
_CONTRACT_PATH = "getContractListInfoServc"           # 계약 (BidPublicInfoService 공통)

# 장기계속 판별에 사용할 필드 키
_LT_KEYS = ["cntrctCnclsMthdNm", "bidNtceNm", "rgstTyNm", "lngTmCntrctYn"]


class G2BAdapter(BaseProcurementAdapter):
    source = "g2b"
    agency_codes = ["G2B"]

    def __init__(self, api_key: str | None = None, timeout: int = 30, rate_limit: float = 1.0):
        self.api_key = api_key or os.getenv("G2B_API_KEY")
        self.timeout = timeout
        self.rate_limit = rate_limit  # 요청 간격(초)
        if not self.api_key:
            raise ValueError("G2B_API_KEY 환경변수가 없습니다.")

    # ------------------------------------------------------------------ #
    # 내부 유틸                                                             #
    # ------------------------------------------------------------------ #

    def _request(self, path: str, params: dict) -> list[dict]:
        """나라장터 API 호출 → items 리스트 반환. 페이지네이션 자동 처리."""
        url = f"{_BASE_URL}/{path}"
        page_no = 1
        results: list[dict] = []

        while True:
            query = {
                "serviceKey": self.api_key,
                "type": "json",
                "numOfRows": 999,
                "pageNo": page_no,
                **params,
            }
            resp = requests.get(url, params=query, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()

            body = data.get("response", {}).get("body", {})
            total_count = int(body.get("totalCount", 0))
            items = body.get("items", [])

            # items 구조 정규화
            if isinstance(items, dict):
                items = items.get("item", [])
            if isinstance(items, dict):   # 단건인 경우
                items = [items]
            items = items or []

            results.extend(items)
            time.sleep(self.rate_limit)

            if len(results) >= total_count or not items:
                break
            page_no += 1

        return results

    def _to_int(self, value) -> int | None:
        if value in (None, "", "-"):
            return None
        try:
            return int(float(str(value).replace(",", "").replace("원", "").strip()))
        except (ValueError, TypeError):
            return None

    def _estimated_price_vat_excl(self, raw: dict) -> tuple[int | None, bool]:
        """추정가격(VAT 제외) 반환. 원본이 VAT 포함이면 /1.1 환산."""
        vat_included = False
        for key in ["asignBdgtAmt", "presmptPrce", "estmtPrce", "totPrdprcNum"]:
            amt = self._to_int(raw.get(key))
            if amt:
                # 원본 표기 확인 (필드명 또는 별도 플래그)
                if raw.get("vatIncldYn") == "Y":
                    vat_included = True
                    amt = int(amt / 1.1)
                return amt, vat_included
        return None, vat_included

    def _is_construction(self, raw: dict) -> bool:
        value = " ".join(str(raw.get(k, "")) for k in ["bsnsDivNm", "bidNtceNm"])
        return "공사" in value

    def _construction_type(self, raw: dict) -> str | None:
        text = " ".join(str(raw.get(k, "")) for k in ["indstrytyNm", "bidNtceNm"])
        if "전문" in text:
            return "전문"
        if "종합" in text:
            return "종합"
        return None

    # ------------------------------------------------------------------ #
    # 공개 인터페이스                                                        #
    # ------------------------------------------------------------------ #

    def fetch_notices(self, since: date, until: date) -> Iterator[dict]:
        params = {
            "inqryBgnDt": since.strftime("%Y%m%d") + "0000",
            "inqryEndDt": until.strftime("%Y%m%d") + "2359",
        }
        for item in self._request(_NOTICE_PATH, params):
            yield item

    def fetch_awards(self, since: date, until: date) -> Iterator[dict]:
        params = {
            "inqryBgnDt": since.strftime("%Y%m%d") + "0000",
            "inqryEndDt": until.strftime("%Y%m%d") + "2359",
        }
        for item in self._request(_AWARD_PATH, params):
            yield item

    def fetch_contracts(self, since: date, until: date) -> Iterator[dict]:
        # 계약 API는 별도 서비스(CntrctInfoService) 사용
        # Phase 1 범위에서 우선 placeholder, 이후 확장
        return iter(())

    def normalize(self, raw: dict) -> dict:
        estimated_price, vat_included = self._estimated_price_vat_excl(raw)
        notice_no  = raw.get("bidNtceNo") or raw.get("ntceNo")
        notice_rev = self._to_int(raw.get("bidNtceOrd")) or 0
        payload_hash = hashlib.sha256(
            str(sorted(raw.items())).encode()
        ).hexdigest()

        return {
            "notice_id":               f"g2b:{notice_no}:{notice_rev}",
            "source":                  self.source,
            "notice_no":               notice_no,
            "notice_rev":              notice_rev,
            "agency_code":             "G2B",
            "title":                   raw.get("bidNtceNm"),
            "work_type":               "공사" if self._is_construction(raw) else raw.get("bsnsDivNm"),
            "construction_type":       self._construction_type(raw),
            "is_long_term_continuing": detect_long_term_from_raw(raw, _LT_KEYS),
            "bid_method":              raw.get("cntrctCnclsMthdNm") or raw.get("bidwinnrDcsnMthdNm"),
            "estimated_price":         estimated_price,
            "vat_included":            vat_included,
            "posted_at":               raw.get("bidNtceDt"),
            "bid_open_at":             raw.get("opengDt") or raw.get("bidClseDt"),
            "status":                  raw.get("bidNtceSttusNm") or "공고중",
            "raw_payload":             raw,
            "source_hash":             payload_hash,
            "collected_at":            None,
        }

    def health_check(self) -> bool:
        try:
            items = self._request(
                _NOTICE_PATH,
                {"inqryBgnDt": "202601010000", "inqryEndDt": "202601012359"},
            )
            return isinstance(items, list)
        except Exception:
            return False
