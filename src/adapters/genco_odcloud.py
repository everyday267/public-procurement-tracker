"""genco_odcloud.py — 발전사 계약 체결 현황 odcloud 수집 믹스인 (Phase 2 Wave B)

KEPCO 빅데이터 API(kepco.py)는 발전 자회사의 '입찰공고'만 제공하고 계약 체결
현황은 주지 않는다(fetch_contracts 빈 결과 → G2B 보완). 그런데 발전사 자체계약은
나라장터(G2B)에도 없어 계약 데이터가 통째로 누락된다
(예: 동서발전 '음성 천연가스 발전소 송전선로 건설공사' 1,050억은 어느 경로에도 없음).

공공데이터포털 '파일데이터 자동변환 오픈API'(api.odcloud.kr)가 남부발전·동서발전의
계약 체결 현황을 공개한다. 본 믹스인이 그 계약분을 fetch_contracts로 끌어와 기존
genco 어댑터(kepco_family.py)의 계약 공백을 메운다. 공고는 KEPCO 빅데이터 경로를
그대로 쓴다(다중 상속 MRO: 믹스인이 KEPCOAdapter보다 앞).

조사 근거: docs/PHASE2_WAVE_B_ODCLOUD_CONTRACTS.md (probe run #191/#192)
  - 연도별 파일이 누적본(KOSPO 2020~, EWP 2016~) → 최신 스냅샷 하나면 과거 전량.
  - 상반기 스냅샷(매년 6월 말까지 반영) → 연 1회 최신 uddi로 갱신.
  - uddi가 연도마다 바뀌므로 ODCLOUD_DATASETS에 신규 연도 추가.
  - 필드 차이: EWP는 구분(공사/용역/물품)·계약번호·예정가격·계약업체 보유,
    KOSPO는 구분이 '구매입찰정보/공사용역입찰정보'뿐이라 계약명 휴리스틱으로 공사 분리.

인증키: data.go.kr 일반 인증키(G2B_API_KEY) 재사용 — KEPCO_API_KEY와 별개.
미설정 시 계약은 건너뛰고 공고만 수집한다(부분 운영 허용, 실행계획 §2.3).
"""
import logging
import os
import urllib.parse
from datetime import date
from typing import Iterator, Optional

import requests

from ..http_client import get_with_retry
from ..long_term_detector import detect_long_term_from_raw

logger = logging.getLogger(__name__)

ODCLOUD_BASE = "https://api.odcloud.kr/api"

# source → {연도: uddi 경로}. 최신 연도 파일이 과거 누적을 모두 포함하므로 수집 시
# 가장 최근 연도 하나만 조회한다. 신규 연도 스냅샷이 공개되면 여기에 추가.
ODCLOUD_DATASETS: dict[str, dict[int, str]] = {
    "kospo": {
        2021: "/15095366/v1/uddi:50fea921-3458-4f38-a63e-f1a932d04a40",
        2022: "/15095366/v1/uddi:35411ee3-53da-4d85-a688-ede46893dcdd",
        2023: "/15095366/v1/uddi:25d4bcde-fe74-4e52-bffb-8dc8c04dcd13",
        2024: "/15095366/v1/uddi:6e670b13-5f30-477c-ab9c-a5116b0392a1",
        2025: "/15095366/v1/uddi:ca0adac0-6047-494c-886f-a2c917fbd49b",
    },
    "ewp": {
        2025: "/15065323/v1/uddi:76402d29-9ed9-4ffe-9197-8dcc89147adc",
    },
}

# source → 발주기관 한글명 (demand_inst 구성용)
ODCLOUD_INST_NAME = {
    "kospo": "한국남부발전",
    "ewp":   "한국동서발전",
}

MIN_CONTRACT_PRICE = 10_000_000_000  # 100억 (공사이행보증서 대상 규모)
_PER_PAGE = 1000
_ODCLOUD_TIMEOUT = 60
_MAX_PAGES = 100  # 안전장치 (최대 10만행)
# 장기계속 판별 대상 필드 (계약명)
_LT_KEYS = ["계약명"]


def _pick(row: dict, *cands: str):
    """필드명 후보(부분일치) 중 실제 존재하는 첫 키의 값. odcloud 필드명이
    '계약금액' / '계약금액(부가세 포함)'처럼 소스마다 달라 부분일치로 흡수한다."""
    for k in row:
        for c in cands:
            if c in k:
                return row[k]
    return None


def _to_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(str(v).replace(",", "").replace("원", "").strip()))
    except (ValueError, TypeError):
        return None


def _to_iso(v) -> Optional[str]:
    """'2021-04-12' / '20210412' 등 → 'YYYY-MM-DD'. 실패 시 None."""
    if v is None:
        return None
    digits = "".join(ch for ch in str(v) if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


class OdcloudContractsMixin:
    """발전사 계약 체결 현황(api.odcloud.kr)을 fetch_contracts로 제공하는 믹스인.

    genco 어댑터(kepco_family.py)에서 KEPCOAdapter보다 **앞에** 상속해 계약 공백을
    메운다. run_monthly는 is_large_construction_contract로 raw 사전 필터 후
    normalize_contract로 정규화하고 _is_target_contract(계약금액 100억↑ 공사)로
    최종 판정한다(EX 어댑터와 동일 연동 규약).
    """

    def _odcloud_key(self) -> Optional[str]:
        # 인코딩키/디코딩키 혼용 대비 unquote (requests가 params를 다시 인코딩).
        key = os.getenv("G2B_API_KEY", "")
        return urllib.parse.unquote(key) if key else None

    def _odcloud_path(self) -> Optional[str]:
        datasets = ODCLOUD_DATASETS.get(self.source)
        if not datasets:
            return None
        return datasets[max(datasets)]  # 최신 연도 = 누적 최다

    # ── fetch ─────────────────────────────────────────────────────────────

    def fetch_contracts(self, since: date, until: date) -> Iterator[dict]:
        """계약 체결 현황을 누적 스냅샷에서 전량 순회 후 계약일자로 기간 필터.

        스냅샷은 연 1회(상반기) 갱신되므로 최신 기간(스냅샷 미반영 구간)은 0건일 수
        있다 — 정상. 백필은 넓은 --since/--until로 한 번에 회수한다.
        """
        key, path = self._odcloud_key(), self._odcloud_path()
        if not key or not path:
            reason = "G2B_API_KEY 미설정" if not key else "odcloud 데이터셋 미등록"
            logger.info("[%s] odcloud 계약 수집 skip (%s) — 공고만 수집", self.source, reason)
            return
        lo, hi = str(since), str(until)
        yielded, scanned = 0, 0
        for page in range(1, _MAX_PAGES + 1):
            data = self._odcloud_get(key, path, page)
            if not data:
                break
            scanned += len(data)
            for raw in data:
                iso = _to_iso(_pick(raw, "계약일"))
                if iso and lo <= iso <= hi:
                    yield raw
                    yielded += 1
            if len(data) < _PER_PAGE:
                break
        logger.info("[%s] odcloud 계약 %s~%s: %d건 (누적 스냅샷 %d행 스캔)",
                    self.source, since, until, yielded, scanned)

    def _odcloud_get(self, key: str, path: str, page: int) -> list:
        """odcloud 단일 페이지 → data 리스트. 429/5xx는 http_client가 재시도."""
        try:
            resp = get_with_retry(
                f"{ODCLOUD_BASE}{path}",
                {"serviceKey": key, "page": page, "perPage": _PER_PAGE,
                 "returnType": "JSON"},
                timeout=_ODCLOUD_TIMEOUT, label=f"{self.source}:odcloud",
            )
        except requests.HTTPError:
            logger.exception("[%s] odcloud 페이지 %d 조회 실패 — 이후 중단", self.source, page)
            return []
        try:
            payload = resp.json()
        except ValueError:
            logger.error("[%s] odcloud 비JSON 응답 (차단/개편 의심): %s",
                         self.source, resp.text[:200])
            return []
        return payload.get("data") or []

    # ── 필터 / 정규화 (run_monthly 연동 규약) ──────────────────────────────

    def _odcloud_is_construction(self, kind: str, name: str) -> bool:
        """구분(EWP: 공사/용역/물품)이 '공사'인지. KOSPO는 서브클래스에서 오버라이드."""
        return kind == "공사"

    def is_large_construction_contract(self, raw: dict) -> bool:
        """공사 + 계약금액 100억↑ (raw 사전 필터). 계약금액이 곧 보증 대상 규모."""
        kind = str(_pick(raw, "구분") or "").strip()
        name = str(_pick(raw, "계약명") or "")
        if not self._odcloud_is_construction(kind, name):
            return False
        amt = _to_int(_pick(raw, "계약금액"))
        return amt is not None and amt >= MIN_CONTRACT_PRICE

    def normalize_contract(self, raw: dict) -> dict:
        """odcloud 계약 raw → 공통 contracts 스키마. 필드는 소스별로 있으면 채운다."""
        office = str(_pick(raw, "담당사업소", "사업소") or "").strip()
        inst = ODCLOUD_INST_NAME.get(self.source, self.source)
        return {
            "source":           self.source,
            "notice_no":        None,   # odcloud 계약엔 공고번호 없음
            "contract_no":      self._clean(_pick(raw, "계약번호")),
            "contract_name":    self._clean(_pick(raw, "계약명")),
            "bsns_div":         "공사",
            "contract_price":   _to_int(_pick(raw, "계약금액")),
            "total_contract_price": None,
            "contracted_at":    _to_iso(_pick(raw, "계약일")),
            "contract_method":  self._clean(_pick(raw, "계약방법")),
            "is_long_term":     "장기계속" if detect_long_term_from_raw(raw, _LT_KEYS) else None,
            "demand_inst":      f"{inst} {office}".strip(),
            "contract_inst":    self._clean(office) or inst,
            "contractor_name":  self._clean(_pick(raw, "계약업체")),
            "contractor_bizno": None,
            "raw_payload":      raw,
        }

    def _clean(self, v) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s if s not in ("", "-") else None
