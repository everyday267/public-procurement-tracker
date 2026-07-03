import os
import json
import hashlib
import logging
import requests
from datetime import date
from typing import Iterator, Optional
from xml.etree import ElementTree as ET

from ..http_client import get_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "http://openapi.ebid.lh.or.kr/ebid.com.openapi.service"
CONSTRUCTION_MIN_PRICE = 10_000_000_000  # 100억 VAT 제외
REQUEST_INTERVAL = 1.0  # 초당 최대 1 req
MAX_RETRIES = 4         # 커넥션 거부/타임아웃 시 지수 백오프 재시도 횟수


class LHAdapter:
    """
    LH e-Bid OpenAPI 어댑터.
    입찰공고(OpenBidInfoList) / 개찰결과(OpenTenderopenList) / 계약현황(OpenContractInfoList)
    3개 엔드포인트를 사용하며, bidNum으로 조인 가능하다.
    """
    source = "lh"
    agency_codes = ["LH"]

    def __init__(self, service_key: Optional[str] = None, timeout: int = 30):
        self.service_key = service_key or os.getenv("LH_API_KEY", "")
        self.timeout = timeout
        if not self.service_key:
            raise ValueError("LH_API_KEY 환경변수 또는 service_key 인자 필요")
        # keep-alive 세션: 매 요청마다 새 커넥션을 열면 LH e-Bid가 거부(Connection
        # refused)하는 패턴이 있어 커넥션을 재사용한다.
        self.session = requests.Session()

    # ── HTTP ──────────────────────────────────────────────────────────────

    def _get(self, endpoint: str, params: dict) -> ET.Element:
        url = f"{BASE_URL}.{endpoint}.dev"
        params = {**params, "serviceKey": self.service_key}
        r = get_with_retry(
            url, params, timeout=self.timeout, session=self.session,
            max_retries=MAX_RETRIES, sleep_before=REQUEST_INTERVAL, label="LH",
        )
        return ET.fromstring(r.text)

    def _items(self, root: ET.Element) -> list[dict]:
        items = []
        for item in root.findall(".//item"):
            d = {}
            for child in list(item):
                d[child.tag] = (child.text or "").strip()
            items.append(d)
        return items

    def _paginate(self, endpoint: str, params: dict, page_size: int = 100) -> Iterator[dict]:
        page = 1
        while True:
            root = self._get(endpoint, {**params, "numOfRows": page_size, "pageNo": page})
            rows = self._items(root)
            for row in rows:
                yield row
            total = int(root.findtext(".//totalCount") or 0)
            logger.debug("[LH] %s page=%d total=%d fetched=%d", endpoint, page, total, page * page_size)
            if page * page_size >= total:
                break
            page += 1

    # ── Fetch ──────────────────────────────────────────────────────────────

    def fetch_notices(self, since: date, until: date) -> Iterator[dict]:
        """입찰공고정보 수집 (tndrbidRegDt 기준)."""
        yield from self._paginate("OpenBidInfoList", {
            "tndrbidRegDtStart": since.strftime("%Y%m%d"),
            "tndrbidRegDtEnd":   until.strftime("%Y%m%d"),
        })

    def fetch_awards(self, since: date, until: date) -> Iterator[dict]:
        """개찰결과정보 수집 (openDtm 기준)."""
        yield from self._paginate("OpenTenderopenList", {
            "openDtmStart": since.strftime("%Y%m%d"),
            "openDtmEnd":   until.strftime("%Y%m%d"),
        })

    def fetch_contracts(self, since: date, until: date) -> Iterator[dict]:
        """계약현황정보 수집 (contractDt 기준)."""
        yield from self._paginate("OpenContractInfoList", {
            "contractDtStart": since.strftime("%Y%m%d"),
            "contractDtEnd":   until.strftime("%Y%m%d"),
        })

    # ── Normalize ──────────────────────────────────────────────────────────

    def normalize_notice(self, raw: dict) -> dict:
        ep = self._to_int(raw.get("presmtPrc"))
        vat = self._to_int(raw.get("addtTax"))
        return {
            "source":                  self.source,
            "notice_id":               f"lh:{raw.get('bidNum')}:1",
            "notice_no":               raw.get("bidNum"),
            "title":                   raw.get("bidnmKor"),
            "work_type":               "공사",
            "construction_type":       self._construction_type(raw),
            "bid_method":              raw.get("sunjungNm"),
            "is_long_term_continuing": self._detect_long_term(raw),
            "estimated_price":         ep,
            "vat_included":            vat is not None and vat > 0,
            "bid_open_at":             self._parse_dt(raw.get("openDtm")),
            "posted_at":               self._parse_dt(raw.get("tndrbidRegDt")),
            "status":                  raw.get("bidProgrsStatus"),
            "zone_hq":                 raw.get("zoneHqCd"),
            "license_conditions":      self._license_summary(raw),
            "vendor_restrictions":     self._vendor_restrictions(raw),
            "raw_payload":             raw,
            "source_hash":             self._hash(raw),
        }

    def normalize_award(self, raw: dict) -> dict:
        return {
            "source":         self.source,
            "notice_no":      raw.get("bidNum"),
            "bidder_name":    raw.get("tndrVndrNm"),
            "bidder_biz_no":  raw.get("taxregno"),
            "award_price":    self._to_int(raw.get("decTndrAmt")),
            "award_rate":     self._to_float(raw.get("invtgtRate")),
            "awarded_at":     self._parse_dt(raw.get("openDtm")),
            "winner_status":  raw.get("vndrSccfBidStatusNm"),
            "expect_price":   self._to_int(raw.get("expectPrc")),
            "design_price":   self._to_int(raw.get("designPrc")),
            "base_price":     self._to_int(raw.get("fdmtlAmt")),
            "lot_num1":       raw.get("decLotNum1"),
            "lot_num2":       raw.get("decLotNum2"),
            "raw_payload":    raw,
        }

    def normalize_contract(self, raw: dict) -> dict:
        return {
            "source":          self.source,
            "notice_no":       raw.get("bidNum"),
            "contract_no":     raw.get("bidNum"),
            "contract_name":   raw.get("ctrctNm"),
            "contract_price":  self._to_int(raw.get("ctrctAmt")),
            "contracted_at":   self._parse_dt(raw.get("ctrctCntrctgDt")),
            "contract_method": raw.get("tndrCtrctMedNm"),
            "contractor_name": raw.get("ctrctVndrNm"),
            "contractor_type": raw.get("ctrctVndrTypeNm"),
            "start_date":      raw.get("initBgnwrkDt"),
            "end_date":        raw.get("finlCompwrkDt"),
            "raw_payload":     raw,
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    def _construction_type(self, raw: dict) -> Optional[str]:
        parts = [raw.get(f"req{i}LicGbNm", "") for i in range(1, 11)]
        txt = " ".join(filter(None, parts))
        if "종합" in txt:
            return "종합"
        if "전문" in txt:
            return "전문"
        return None

    def _license_summary(self, raw: dict) -> list[dict]:
        result = []
        for i in range(1, 11):
            gb  = raw.get(f"req{i}LicGbNm", "")
            bsn = raw.get(f"req{i}BsncatGrpNm", "")
            cond = raw.get(f"req{i}LicctNm", "")
            lics = [raw.get(f"req{i}Reqlic{j}Nm", "") for j in range(1, 11)]
            lics = [x for x in lics if x]
            if gb or bsn or lics:
                result.append({
                    "seq": i, "type": gb, "group": bsn,
                    "condition": cond, "licenses": lics
                })
        return result

    def _vendor_restrictions(self, raw: dict) -> list[str]:
        return [raw.get(f"vndrrstrctNm{i}", "") for i in range(1, 5)
                if raw.get(f"vndrrstrctNm{i}")]

    def _detect_long_term(self, raw: dict) -> bool:
        import re
        text = " ".join(str(raw.get(k, "")) for k in
                        ["bidnmKor", "sunjungNm", "tndrCtrctMedCd"])
        return bool(re.search(r"장기계속|L/T|차수계약", text, re.IGNORECASE))

    def passes_filter(self, normalized: dict) -> bool:
        ep = normalized.get("estimated_price")
        return ep is not None and ep >= CONSTRUCTION_MIN_PRICE

    def health_check(self) -> bool:
        try:
            today = date.today()
            root = self._get("OpenBidInfoList", {
                "numOfRows": 1, "pageNo": 1,
                "tndrbidRegDtStart": today.strftime("%Y%m%d"),
                "tndrbidRegDtEnd":   today.strftime("%Y%m%d"),
            })
            return root.findtext(".//resultCode") == "00"
        except Exception:
            return False

    def _to_int(self, v) -> Optional[int]:
        try:
            return int(float(str(v).replace(",", ""))) if v not in (None, "") else None
        except Exception:
            return None

    def _to_float(self, v) -> Optional[float]:
        try:
            return float(str(v).replace(",", "")) if v not in (None, "") else None
        except Exception:
            return None

    def _parse_dt(self, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v = v.strip().replace(" ", "T")
        return v if len(v) >= 8 else None

    def _hash(self, raw: dict) -> str:
        return hashlib.md5(
            json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
