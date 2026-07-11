"""kiscon.py — KISCON 건설공사대장 통보 통계 수집 클라이언트 (검증 층 2).

국토교통부 키스콘 건설공사대장 통보 통계서비스 (data.go.kr, 자동승인):
  - Base URL : http://apis.data.go.kr/1613000/ConStatInfoSvc
  - StatAmt  : 통보 금액 리스트 — 일별×지역×발주자×도급 집계액 (단위: 억원)
  - StatCnt  : 통보 건수 리스트 — 동일 축 건수
  - 건별 리스트 오퍼레이션 — probe_kiscon.py로 엔드포인트명·필드 확정 후
    KISCON_RECORDS_OP 환경변수(또는 인자)로 활성화한다.

데이터 제공 시작일: 2020-07-15 (API 스펙 명시).

이 소스는 notices/awards/contracts를 만들지 않는 통계 소스이므로
BaseProcurementAdapter를 상속하지 않는다. 페이지네이션·키 처리 방식은
g2b_opnstd.py의 data.go.kr 관례를 따른다.
"""
import hashlib
import logging
import os
import time
from datetime import date, timedelta
from typing import Dict, Iterator, List, Optional, Tuple

import requests
from dateutil.relativedelta import relativedelta

from .http_client import get_with_retry
from urllib.parse import unquote

logger = logging.getLogger(__name__)

_BASE_URL = "http://apis.data.go.kr/1613000/ConStatInfoSvc"
STAT_AMT_OP = "StatAmt"
STAT_CNT_OP = "StatCnt"

# 데이터 제공 시작일 (스펙: "2020년 7월 15일 이후 데이터제공")
KISCON_DATA_START = date(2020, 7, 15)

# 무한 순회 방지 (g2b_opnstd과 동일한 안전장치)
_MAX_PAGES = 2000

# 발주자구분: 공공=0, 민간(법인)=1, 민간(개인)=2, 전체=3 / 도급구분: 원=1, 하=2
BALJU_PUBLIC = "0"
DOGUB_PRIME = "1"

# 건별 레코드 계약금액 단위 → 원 환산 계수. StatAmt는 억원 단위이므로 건별도
# 동일 단위일 가능성이 있다. probe로 확정 후 필요 시 수정한다. (기본: 원 그대로)
RECORDS_PRICE_UNIT = 1

# 건별 리스트 응답 필드명 후보. 스펙 확정 전이므로 후보군에서 첫 일치 키를 쓴다.
_RECORD_FIELD_CANDIDATES = {
    "work_name":       ["constNm", "workNm", "cmplNm", "constructionName", "sjName", "constName"],
    "contractor_name": ["cmpNm", "bizNm", "companyName", "frmNm", "entrpsNm"],
    "contract_price":  ["contAmt", "cntrctAmt", "contractAmount", "amt", "dogubAmt"],
    "start_date":      ["startDate", "beginDate", "strtDate", "sDate"],
    "end_date_plan":   ["endDate", "cmplDate", "eDate", "compYmd"],
}


def _pick(raw: dict, candidates: List[str]):
    for key in candidates:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _to_float(value) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _to_int(value) -> Optional[int]:
    f = _to_float(value)
    return int(f) if f is not None else None


class KisconClient:
    """ConStatInfoSvc 호출 클라이언트."""

    def __init__(self, api_key=None, timeout=30, rate_limit=0.2, records_op=None):
        # type: (Optional[str], int, float, Optional[str]) -> None
        raw_key = api_key or os.getenv("KISCON_API_KEY")
        if not raw_key:
            raise ValueError("KISCON_API_KEY 환경변수가 없습니다.")
        # data.go.kr 키 이중 인코딩 방지 (g2b_opnstd.py와 동일한 처리)
        self.api_key = unquote(raw_key)
        self.timeout = timeout
        self.rate_limit = rate_limit
        # 건별 리스트 오퍼레이션명 — probe로 확정 전까지 미설정(None)이면 skip
        self.records_op = records_op or os.getenv("KISCON_RECORDS_OP") or None
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    # 저수준 호출                                                          #
    # ------------------------------------------------------------------ #

    def _request(self, operation, params, max_pages=_MAX_PAGES):
        # type: (str, dict, int) -> Iterator[Dict]
        """페이지 단위 스트리밍 호출 (g2b_opnstd._request와 동일한 관례)."""
        url = "{}/{}".format(_BASE_URL, operation)
        page_no = 1
        fetched = 0

        while True:
            query = {
                "ServiceKey": self.api_key,   # 스펙 표기: ServiceKey (대문자 S)
                "_type": "json",
                "numOfRows": 999,
                "pageNo": page_no,
            }
            query.update(params)
            resp = get_with_retry(
                url, query, timeout=self.timeout, session=self.session, label="KISCON",
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

            for item in items:
                yield item
            fetched += len(items)

            if fetched >= total_count or not items:
                break
            if page_no >= max_pages:
                logger.warning("[KISCON] %s max_pages=%d 도달, 중단 (total=%d fetched=%d)",
                               operation, max_pages, total_count, fetched)
                break
            page_no += 1
            time.sleep(self.rate_limit)

    def _monthly_chunks(self, since, until):
        # type: (date, date) -> Iterator[Tuple[date, date]]
        """조회범위 상한이 확인되지 않았으므로 월 단위로 분할한다 (안전 기본값)."""
        cursor = since
        while cursor <= until:
            chunk_end = min(
                (cursor.replace(day=1) + relativedelta(months=1)) - timedelta(days=1),
                until,
            )
            yield cursor, chunk_end
            cursor = chunk_end + timedelta(days=1)

    # ------------------------------------------------------------------ #
    # 오퍼레이션별 fetch                                                    #
    # ------------------------------------------------------------------ #

    def fetch_stats(self, operation, since, until, balju=None, dogub=None, area=None):
        # type: (str, date, date, Optional[str], Optional[str], Optional[str]) -> Iterator[Dict]
        """StatAmt/StatCnt 공용 — 월 청킹 + 페이지네이션."""
        since = max(since, KISCON_DATA_START)
        for s, e in self._monthly_chunks(since, until):
            params = {"sDate": s.strftime("%Y%m%d"), "eDate": e.strftime("%Y%m%d")}
            if balju is not None:
                params["balju"] = balju
            if dogub is not None:
                params["dogub"] = dogub
            if area is not None:
                params["area"] = area
            yield from self._request(operation, params)

    def fetch_records(self, since, until, balju=BALJU_PUBLIC, dogub=DOGUB_PRIME):
        # type: (date, date, str, str) -> Iterator[Dict]
        """건별 통보 리스트. 오퍼레이션명 미확정(records_op=None)이면 빈 이터레이터."""
        if not self.records_op:
            logger.info("[KISCON] 건별 리스트 오퍼레이션 미설정 (KISCON_RECORDS_OP) — 건너뜀")
            return
        yield from self.fetch_stats(self.records_op, since, until, balju=balju, dogub=dogub)

    # ------------------------------------------------------------------ #
    # 정규화                                                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def normalize_stat(raw, kind):
        # type: (dict, str) -> dict
        """StatAmt/StatCnt 응답 → kiscon_stats 행. kind: 'amt' | 'cnt'"""
        row = {
            "noti_date":  str(raw.get("notiDate", "")),
            "area_code":  str(raw.get("areaCode", "")),
            "balju_code": str(raw.get("baljuCode", "")),
            "dogub_code": str(raw.get("dogubCode", "")),
            "area_name":  raw.get("areaName"),
            "raw_payload": raw,
        }
        if kind == "amt":
            row["amt_100m"] = _to_float(raw.get("amt"))
        else:
            row["cnt"] = _to_int(raw.get("cnt") or raw.get("count") or raw.get("cntrctCnt"))
        return row

    @staticmethod
    def normalize_record(raw):
        # type: (dict) -> dict
        """건별 응답 → kiscon_records 행. 필드명은 후보군 매칭 (probe 후 확정)."""
        price_raw = _to_int(_pick(raw, _RECORD_FIELD_CANDIDATES["contract_price"]))
        price = price_raw * RECORDS_PRICE_UNIT if price_raw is not None else None
        row = {
            "noti_date":       str(raw.get("notiDate", "")),
            "area_code":       str(raw.get("areaCode", "")),
            "balju_code":      str(raw.get("baljuCode", "")),
            "dogub_code":      str(raw.get("dogubCode", "")),
            "work_name":       _pick(raw, _RECORD_FIELD_CANDIDATES["work_name"]),
            "contractor_name": _pick(raw, _RECORD_FIELD_CANDIDATES["contractor_name"]),
            "contract_price":  price,
            "start_date":      _pick(raw, _RECORD_FIELD_CANDIDATES["start_date"]),
            "end_date_plan":   _pick(raw, _RECORD_FIELD_CANDIDATES["end_date_plan"]),
            "raw_payload":     raw,
        }
        # 통보번호류 고유키가 스펙에 없으므로 내용 해시로 record_key 생성 (멱등 upsert)
        digest_src = "|".join(str(row.get(k) or "") for k in
                              ("noti_date", "work_name", "contractor_name", "contract_price"))
        row["record_key"] = hashlib.sha1(digest_src.encode("utf-8")).hexdigest()
        return row


# ---------------------------------------------------------------------- #
# 수집 진입점                                                               #
# ---------------------------------------------------------------------- #

# 수집 대상 (balju, dogub) 셀: 공공×원도급이 주력, 공공×하도급·전체×원도급은 참고용
DEFAULT_STAT_CELLS = [
    (BALJU_PUBLIC, DOGUB_PRIME),
    (BALJU_PUBLIC, "2"),
    ("3", DOGUB_PRIME),
]


def collect_kiscon_stats(conn, client, since, until, cells=DEFAULT_STAT_CELLS):
    # type: (object, KisconClient, date, date, list) -> int
    """StatAmt + StatCnt를 수집해 kiscon_stats에 병합 upsert.

    두 오퍼레이션의 셀 축이 같으므로 PK로 메모리 병합 후 한 번에 upsert한다
    (INSERT OR REPLACE라 따로 넣으면 한쪽 컬럼이 NULL로 덮이기 때문).
    """
    from .db import upsert_kiscon_stats

    merged = {}  # pk tuple -> row
    for balju, dogub in cells:
        for raw in client.fetch_stats(STAT_AMT_OP, since, until, balju=balju, dogub=dogub):
            row = client.normalize_stat(raw, "amt")
            pk = (row["noti_date"], row["area_code"], row["balju_code"], row["dogub_code"])
            merged.setdefault(pk, row).update({"amt_100m": row["amt_100m"]})
        try:
            for raw in client.fetch_stats(STAT_CNT_OP, since, until, balju=balju, dogub=dogub):
                row = client.normalize_stat(raw, "cnt")
                pk = (row["noti_date"], row["area_code"], row["balju_code"], row["dogub_code"])
                if pk in merged:
                    merged[pk]["cnt"] = row["cnt"]
                else:
                    merged[pk] = row
        except requests.HTTPError as e:
            # StatCnt 스펙 미확정 — 미지원(4xx)이어도 금액 수집은 유지한다
            logger.warning("[KISCON] StatCnt 호출 실패 (스펙 미확정): %s", e)

    rows = list(merged.values())
    for r in rows:
        r.setdefault("amt_100m", None)
        r.setdefault("cnt", None)
    return upsert_kiscon_stats(conn, rows)


def collect_kiscon_records(conn, client, since, until):
    # type: (object, KisconClient, date, date) -> int
    """건별 통보 리스트 수집 (공공×원도급). 오퍼레이션 미확정이면 0 반환."""
    from .db import upsert_kiscon_records

    rows = [client.normalize_record(raw) for raw in client.fetch_records(since, until)]
    return upsert_kiscon_records(conn, rows)
