"""kosis.py — KOSIS 건설업 통계 수집 클라이언트 (검증 보조 소스).

KOSIS OpenAPI statisticsParameterData.do(getList)로 종합·전문·전기 건설업의
**공사규모별 × 발주기관별 계약실적**을 수집한다. KISCON StatAmt에 없던
'공사규모(금액구간)' 축을 제공하므로 100억↑ 필터·업종 정렬 검증에 쓴다.

등록 표 (사용자 제공 URL 기준):
  종합건설업 : orgId=365 tblId=DT_365001_A072 itmId=16365AAD2,16365AAB6 (objL1~3)
  전문건설업 : orgId=366 tblId=TX_36601_A089  itmId=16366AAA0,16366AAA1 (objL1~3)
  전기공사업 : orgId=370 tblId=DT_370001_A010 itmId=T001,16370AAD3     (objL1~2)

getList 응답은 페이지 없이 요청 기간(newEstPrdCnt) 전체를 배열로 반환한다.
각 행은 분류축을 C1~C8 (코드 Cn / 값 Cn_NM / 축이름 Cn_OBJ_NM)으로 담고,
값은 DT, 기간은 PRD_DE에 있다. 오류 시 배열이 아니라 {err, errMsg} 객체가 온다.

키: KOSIS_API_KEY 환경변수 (URL에 담기던 apiKey). data.go.kr과 달리 URL 인코딩
이슈는 없으나, requests가 파라미터를 인코딩하므로 그대로 전달한다.
"""
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

from .http_client import get_with_retry

logger = logging.getLogger(__name__)

_BASE_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"

# getList 응답에서 항상 확보할 출력 필드 (사용자 URL과 동일 구성)
_OUTPUT_FIELDS = ("ORG_ID TBL_ID TBL_NM OBJ_ID OBJ_NM NM ITM_ID ITM_NM "
                  "UNIT_NM PRD_SE PRD_DE LST_CHN_DE")


class KosisError(RuntimeError):
    """KOSIS가 배열 대신 {err, errMsg} 오류 객체를 반환했을 때."""


@dataclass(frozen=True)
class KosisTable:
    key: str
    org_id: str
    tbl_id: str
    itm_ids: tuple
    obj_levels: int          # ALL을 채울 분류 레벨 수 (objL1..objL{n})
    industry: str            # 종합 | 전문 | 전기
    prd_se: str = "Y"        # 사용자 URL 기본값(Y). 월별 필요 시 호출에서 override
    label: str = ""


# 사용자 제공 3개 URL의 파라미터를 그대로 등록한다.
# 축 구성(probe 실측): 종합/전문 = 발주기관별(C1)·공사규모별(C2)·월별(C3),
# 전기 = 공사규모별(C1)·발주기관별(C2). itm_nm·단위는 응답에서 읽는다
# (종합 금액=십억원, 전문·전기 금액=백만원 / 건수=건).
KOSIS_TABLES: Dict[str, KosisTable] = {
    "gen": KosisTable(
        key="gen", org_id="365", tbl_id="DT_365001_A072",
        itm_ids=("16365AAD2", "16365AAB6"), obj_levels=3, industry="종합",
        label="종합건설업 공사규모별 월별 발주기관별 계약실적"),
    "spec": KosisTable(
        key="spec", org_id="366", tbl_id="TX_36601_A083",
        itm_ids=("16001", "16366AAA2"), obj_levels=2, industry="전문",
        label="전문건설업 공사규모별 발주기관별 계약실적 (100억↑ 구간 포함)"),
    "elec": KosisTable(
        key="elec", org_id="370", tbl_id="DT_370001_A010",
        itm_ids=("T001", "16370AAD3"), obj_levels=2, industry="전기",
        label="전기공사업 공사규모별 발주기관별 공사건수 및 실적"),
}


def _to_float(value) -> Optional[float]:
    """DT 값 파싱. '-', '', 'X'(비공개) 등은 None."""
    if value in (None, "", "-", "X", "x"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


class KosisClient:
    def __init__(self, api_key=None, timeout=30, output_fields=_OUTPUT_FIELDS):
        # type: (Optional[str], int, str) -> None
        key = api_key or os.getenv("KOSIS_API_KEY")
        if not key:
            raise ValueError("KOSIS_API_KEY 환경변수가 없습니다.")
        self.api_key = key
        self.timeout = timeout
        self.output_fields = output_fields
        self.session = requests.Session()

    def _params(self, table, prd_se, num_periods, start_prd, end_prd):
        # type: (KosisTable, str, Optional[int], Optional[str], Optional[str]) -> dict
        # objL1..objL8: 표의 분류 레벨 수만큼 ALL, 나머지는 빈 값.
        obj = {}
        for i in range(1, 9):
            obj["objL{}".format(i)] = "ALL" if i <= table.obj_levels else ""
        # itmId·objL·outputFields는 '+'(공백)로 구분 — requests가 공백을 인코딩한다.
        params = {
            "method": "getList",
            "apiKey": self.api_key,
            "orgId": table.org_id,
            "tblId": table.tbl_id,
            "itmId": " ".join(table.itm_ids),
            "format": "json",
            "jsonVD": "Y",
            "prdSe": prd_se or table.prd_se,
            "outputFields": self.output_fields,
        }
        params.update(obj)
        if start_prd and end_prd:
            params["startPrdDe"] = start_prd
            params["endPrdDe"] = end_prd
        else:
            params["newEstPrdCnt"] = num_periods or 10
        return params

    def fetch_table(self, table, prd_se=None, num_periods=10, start_prd=None, end_prd=None):
        # type: (KosisTable, Optional[str], Optional[int], Optional[str], Optional[str]) -> List[dict]
        """단일 표 getList 호출. 배열 반환, 오류 객체면 KosisError."""
        params = self._params(table, prd_se, num_periods, start_prd, end_prd)
        resp = get_with_retry(_BASE_URL, params, timeout=self.timeout,
                              session=self.session, label="KOSIS")
        data = resp.json()
        if isinstance(data, dict):
            msg = data.get("errMsg") or data.get("err") or str(data)
            raise KosisError("[{}] {}".format(table.key, msg))
        return data or []

    @staticmethod
    def normalize(raw, table):
        # type: (dict, KosisTable) -> dict
        """getList 행 → kosis_stats 행. C1~C3의 축이름/코드/값을 보존한다."""
        row = {
            "org_id":   str(raw.get("ORG_ID", table.org_id)),
            "tbl_id":   str(raw.get("TBL_ID", table.tbl_id)),
            "industry": table.industry,
            "prd_se":   raw.get("PRD_SE"),
            "prd_de":   str(raw.get("PRD_DE", "")),
            "itm_id":   str(raw.get("ITM_ID", "")),
            "itm_nm":   raw.get("ITM_NM"),
            "unit_nm":  raw.get("UNIT_NM"),
            "dt":       _to_float(raw.get("DT")),
            "raw_payload": raw,
        }
        for n in (1, 2, 3):
            row["c{}_obj".format(n)] = raw.get("C{}_OBJ_NM".format(n))
            row["c{}_code".format(n)] = str(raw.get("C{}".format(n), "") or "")
            row["c{}_nm".format(n)] = raw.get("C{}_NM".format(n))
        return row


def _fetch_table_resilient(client, table, prd_se, num_periods, attempts=3, pause=1.5):
    # type: (KosisClient, KosisTable, Optional[str], int, int, float) -> List[dict]
    """표 단위 회복 수집. KOSIS는 유효한 요청에도 간헐적으로
    '필수요청변수값이 누락되었습니다' 오류 객체(HTTP 200)를 반환하므로
    (get_with_retry는 이를 재시도하지 않음) 여기서 표 단위로 재시도한다.
    끝까지 실패하면 마지막 수단으로 num_periods=1로 축소 재시도한다."""
    import time

    last = None
    for i in range(attempts):
        try:
            return client.fetch_table(table, prd_se=prd_se, num_periods=num_periods)
        except (KosisError, requests.RequestException) as e:
            last = e
            logger.warning("[KOSIS] %s 시도 %d/%d 실패: %s", table.key, i + 1, attempts, e)
            time.sleep(pause)
    if num_periods != 1:
        logger.warning("[KOSIS] %s num_periods=1로 축소 재시도", table.key)
        try:
            return client.fetch_table(table, prd_se=prd_se, num_periods=1)
        except (KosisError, requests.RequestException) as e:
            last = e
    raise last


def collect_kosis(conn, client, tables=None, prd_se=None, num_periods=10):
    # type: (object, KosisClient, Optional[List[str]], Optional[str], int) -> int
    """등록 표(기본 3종)를 수집해 kosis_stats에 upsert. 수집 행수 반환.

    한 표가 실패해도 나머지는 계속 진행한다 (run_monthly 부분수집 정책과 일관).
    """
    from .db import upsert_kosis_stats

    keys = tables or list(KOSIS_TABLES)
    total = 0
    for key in keys:
        table = KOSIS_TABLES[key]
        try:
            raw = _fetch_table_resilient(client, table, prd_se, num_periods)
        except (KosisError, requests.RequestException) as e:
            logger.warning("[KOSIS] %s 수집 최종 실패: %s", key, e)
            continue
        rows = [client.normalize(r, table) for r in raw]
        n = upsert_kosis_stats(conn, rows)
        logger.info("[KOSIS] %s(%s) %d행 수집", key, table.industry, n)
        total += n
    return total


# ---------------------------------------------------------------------- #
# 저장 데이터 요약 (분류축 매핑 확인용)                                       #
# ---------------------------------------------------------------------- #

# 분류축 이름 판별 키워드 (표마다 축 순서가 달라 이름으로 찾는다).
# 실측(probe) 확인: 종합/전문 = 발주기관별(C1)·공사규모별(C2)·월별(C3),
# 전기 = 공사규모별(C1)·발주기관별(C2). 월별은 기간이 아니라 분류축이므로
# 합계와 월을 함께 더하면 2배 중복된다 → 월 축을 별도 식별해 처리한다.
_SCALE_KEYWORDS = ("규모",)
_AGENCY_KEYWORDS = ("발주",)
_MONTH_KEYWORDS = ("월",)

# 월 축의 "전체" 멤버 (표마다 표기가 다르다). 연간 대조 시 이 값만 사용한다.
_MONTH_TOTAL = ("합계", "계", "전체")

# 금액 단위 → 원(KRW) 환산 계수. 표마다 단위가 다르다(종합=십억원, 전문·전기=백만원).
_UNIT_TO_KRW = {
    "원": 1, "천원": 1_000, "만원": 10_000, "백만원": 1_000_000,
    "천만원": 10_000_000, "억원": 100_000_000, "십억원": 1_000_000_000,
    "백억원": 10_000_000_000, "천억원": 100_000_000_000, "조원": 1_000_000_000_000,
}


def amount_to_krw(dt, unit_nm):
    # type: (Optional[float], Optional[str]) -> Optional[float]
    """금액 dt를 unit_nm 기준으로 원(KRW)으로 환산. 건수 등 비금액 단위는 None."""
    if dt is None:
        return None
    factor = _UNIT_TO_KRW.get((unit_nm or "").strip())
    return dt * factor if factor else None


# 공사규모 구간 라벨의 하한(단위: 억원) 추출용. "100억원이상"·"100억~300억"·
# "4000만원 미만"·"5백만원이상" 등에서 첫 수치+단위를 하한으로 읽는다.
_EOK_UNIT = {"조": 10_000.0, "천억": 1_000.0, "백억": 100.0, "억": 1.0,
             "천만": 0.1, "백만": 0.01, "십만": 0.001, "만": 0.0001}
# 긴 단위(천억·백억·천만·백만·십만) 우선 매칭
_UNIT_RE = r"(조|천억|백억|억|천만|백만|십만|만)"


def scale_lower_bound_eok(label):
    # type: (Optional[str]) -> Optional[float]
    """공사규모 라벨의 하한을 억원 단위로 반환. 합계/미상은 None, '미만'만 있는
    최하 구간은 0.

    범위 라벨은 하한(앞 값)을 읽는다 — '50~100억'·'50억~100억'→50, '100억~300억'→100.
    ('이상'/'미만' 단일 구간: '100억원이상'→100, '4000만원미만'→0)
    """
    import re
    if label is None:
        return None
    s = str(label).replace(" ", "").replace(",", "")
    if any(t in s for t in ("합계", "소계", "계")) and not any(c.isdigit() for c in s):
        return None

    if "~" in s:
        # 범위: 앞 값이 하한. 단위는 앞쪽에 없으면 전체에서 찾는다(예: '50~100억').
        left = s.split("~")[0]
        mnum = re.search(r"\d+(?:\.\d+)?", left)
        if not mnum:
            return None
        munit = re.search(_UNIT_RE, left) or re.search(_UNIT_RE, s)
        if not munit:
            return None
        return float(mnum.group()) * _EOK_UNIT[munit.group(1)]

    m = re.search(r"(\d+(?:\.\d+)?)\s*" + _UNIT_RE, s)
    if not m:
        return None
    value = float(m.group(1)) * _EOK_UNIT[m.group(2)]
    # '미만'만 있는 최하 구간(예: 4000만원미만)은 하한 0
    if "미만" in s and "이상" not in s and "초과" not in s:
        return 0.0
    return value


def dimension_labels(conn, industry=None):
    # type: (object, Optional[str]) -> Dict[str, List[str]]
    """저장된 kosis_stats의 분류축(obj)별 멤버(nm) 목록. 어느 C가 공사규모/
    발주기관인지 사람이 확인하거나 매핑 테이블을 짤 때 쓴다."""
    where = "WHERE industry = ?" if industry else ""
    args = (industry,) if industry else ()
    labels = {}  # obj_name -> ordered unique members
    for n in (1, 2, 3):
        cur = conn.execute(
            "SELECT DISTINCT c{n}_obj AS obj, c{n}_nm AS nm FROM kosis_stats "
            "{where} AND c{n}_obj IS NOT NULL".format(
                n=n, where=where or "WHERE 1=1"),
            args,
        )
        for r in cur.fetchall():
            if r["obj"]:
                labels.setdefault(r["obj"], [])
                if r["nm"] and r["nm"] not in labels[r["obj"]]:
                    labels[r["obj"]].append(r["nm"])
    return labels


def _match_dim(obj, keywords):
    # type: (Optional[str], tuple) -> bool
    return bool(obj) and any(k in obj for k in keywords)


def _roles(row):
    # type: (dict) -> tuple
    """행의 C1~C3에서 (공사규모 멤버, 발주기관 멤버, 월 멤버)를 이름으로 식별."""
    scale = agency = month = None
    for n in (1, 2, 3):
        obj = row.get("c{}_obj".format(n))
        nm = row.get("c{}_nm".format(n))
        if not obj:
            continue
        if _match_dim(obj, _SCALE_KEYWORDS) and scale is None:
            scale = nm
        elif _match_dim(obj, _AGENCY_KEYWORDS) and agency is None:
            agency = nm
        elif _match_dim(obj, _MONTH_KEYWORDS) and month is None:
            month = nm
    return scale, agency, month


def scale_agency_summary(conn, industry, itm_nm_like=None, month=None):
    # type: (object, str, Optional[str], Optional[str]) -> List[dict]
    """공사규모 × 발주기관 피벗. 축은 이름으로 자동 식별한다.

    month 처리 (종합·전문은 월별 축이 있어 합계+월이 함께 저장됨):
      - None  : 연간 합계만 (월 합계 행만) — 중복 없음, 대조 기본값
      - '1월' : 해당 월만
      - '*'   : 월 필터 없음 (행 그대로, 중복 주의)
    금액 항목이면 krw(원 환산)을 함께 반환한다.
    """
    q = "SELECT * FROM kosis_stats WHERE industry = ?"
    args = [industry]
    if itm_nm_like:
        q += " AND itm_nm LIKE ?"
        args.append("%{}%".format(itm_nm_like))
    rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    out = []
    for r in rows:
        scale, agency, mon = _roles(r)
        if mon is not None:                      # 월별 축이 있는 표(종합·전문)
            if month is None:
                if mon not in _MONTH_TOTAL:
                    continue                     # 연간 합계 행만
            elif month != "*" and mon != month:
                continue
        if scale is None and agency is None:
            continue
        out.append({
            "prd_de": r["prd_de"], "itm_nm": r["itm_nm"], "unit_nm": r["unit_nm"],
            "scale": scale, "agency": agency, "month": mon,
            "dt": r["dt"], "krw": amount_to_krw(r["dt"], r["unit_nm"]),
        })
    return out


def scale_brackets(conn, industry):
    # type: (object, str) -> List[dict]
    """저장된 공사규모 구간과 그 하한(억원)을 정렬해 반환 — 구간 분류 검증용.
    반환: [{scale, lower_eok}] (lower_eok 오름차순, None은 뒤로)."""
    seen = {}
    for r in scale_agency_summary(conn, industry, month="*"):
        s = r["scale"]
        if s is not None and s not in seen:
            seen[s] = scale_lower_bound_eok(s)
    return sorted(({"scale": s, "lower_eok": lb} for s, lb in seen.items()),
                  key=lambda x: (x["lower_eok"] is None, x["lower_eok"] or 0))


# 공공 발주기관 집합 (probe 확인: 세 표 공통 라벨). 전기의 '한국전력'은 별도
# 표기되나 종합·전문 대조에는 쓰지 않는다(전기는 KISCON·핵심 대조 대상 외).
PUBLIC_AGENCIES = frozenset({"정부기관", "지방자치단체", "공공단체", "공기업"})


def _scale_scheme(scales):
    # type: (set) -> str
    """공사규모 구간 스킴 판별.
    'disjoint'  : 범위형(종합·전문, '100~200억미만' 등) → 구간 합산 가능
    'cumulative': 누적형(전기, '100억이상' 등 'N이상'만) → 합산 금지(구간 자체가 총합)
    """
    members = [s for s in scales
               if s and s not in ("합계", "소계", "계")]
    if not members:
        return "disjoint"
    if all(m.endswith("이상") and "~" not in m and "미만" not in m for m in members):
        return "cumulative"
    return "disjoint"


def ge_threshold_amount(conn, industry, min_eok=100, agencies=None, month=None, year=None):
    # type: (object, str, float, Optional[set], Optional[str], Optional[str]) -> dict
    """공사규모 하한 ≥ min_eok억 금액(원) 합계. 범위형/누적형 스킴을 구분한다.

    - 범위형(종합·전문): 하한 ≥ min_eok인 모든 구간을 합산 (구간은 서로 배타적).
    - 누적형(전기): 'N이상' 구간은 서로 포함관계 → 합산 금지. min_eok 이상 중
      하한이 가장 작은 단일 구간이 곧 '≥min_eok 총합'이다(예: '100억이상').
    agencies 지정 시 해당 발주기관만, year 지정 시 해당 연도만. 금액 항목만.
    반환: {krw, brackets, agencies, scheme}.
    """
    # 금액 항목은 항목명이 아니라 '원 환산 가능한 단위'(krw not None)로 식별한다
    # (표마다 항목명이 '금액'/'계약액' 등으로 달라도 안전).
    rows = [r for r in scale_agency_summary(conn, industry, month=month)
            if r["krw"] is not None and (year is None or r["prd_de"] == year)]
    if agencies is not None:
        rows = [r for r in rows if r["agency"] in agencies]
    scheme = _scale_scheme({r["scale"] for r in rows})

    if scheme == "cumulative":
        # min_eok 이상 구간 중 하한이 가장 작은 것을 선택 (그 자체가 누적 총합)
        eligible = sorted({(scale_lower_bound_eok(r["scale"]), r["scale"]) for r in rows
                           if (scale_lower_bound_eok(r["scale"]) or -1) >= min_eok})
        if not eligible:
            return {"krw": 0.0, "brackets": set(), "agencies": set(), "scheme": scheme}
        target = eligible[0][1]
        sel = [r for r in rows if r["scale"] == target]
    else:
        sel = [r for r in rows
               if (scale_lower_bound_eok(r["scale"]) or -1) >= min_eok]

    return {
        "krw": sum(r["krw"] for r in sel),
        "brackets": {r["scale"] for r in sel},
        "agencies": {r["agency"] for r in sel},
        "scheme": scheme,
    }


def ge100_public_by_year(conn, industry, min_eok=100):
    # type: (object, str, float) -> Dict[str, float]
    """연도별 '공공 발주 × ≥min_eok억' 금액(원). {year: krw}."""
    years = {r["prd_de"] for r in scale_agency_summary(conn, industry)}
    out = {}
    for y in years:
        amt = ge_threshold_amount(conn, industry, min_eok=min_eok,
                                  agencies=PUBLIC_AGENCIES, year=y)
        out[y] = amt["krw"]
    return out


def main():
    # type: () -> int
    import argparse

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    from .db import get_connection, ensure_schema

    ap = argparse.ArgumentParser(description="KOSIS 건설업 통계 수집")
    ap.add_argument("--db", default="procurement.db")
    ap.add_argument("--tables", help="수집 표 (쉼표구분: gen,spec,elec). 비우면 전체")
    ap.add_argument("--periods", type=int, default=10, help="최근 N개 기간 (newEstPrdCnt)")
    ap.add_argument("--prd-se", dest="prd_se", help="기간구분 Y|M|Q (기본 표별 등록값)")
    ap.add_argument("--skip-fetch", action="store_true", help="수집 생략, 저장분 요약만")
    args, _ = ap.parse_known_args()

    conn = get_connection(args.db)
    ensure_schema(conn)

    if not args.skip_fetch:
        client = KosisClient()
        tables = args.tables.split(",") if args.tables else None
        n = collect_kosis(conn, client, tables=tables, prd_se=args.prd_se,
                          num_periods=args.periods)
        logger.info("KOSIS 총 %d행 수집", n)

    for key, table in KOSIS_TABLES.items():
        labels = dimension_labels(conn, industry=table.industry)
        if not labels:
            continue
        logger.info("[%s] 분류축:", table.industry)
        for obj, mem in labels.items():
            logger.info("    %s (%d): %s", obj, len(mem), mem)
        # 100억↑ 구간 분류 결과 — 사용자 검증용
        brackets = scale_brackets(conn, table.industry)
        ge = [b["scale"] for b in brackets
              if b["lower_eok"] is not None and b["lower_eok"] >= 100]
        logger.info("    → 공사규모 하한(억): %s",
                    [(b["scale"], b["lower_eok"]) for b in brackets])
        logger.info("    → 100억↑로 분류된 구간: %s", ge or "(없음)")
        amt = ge_threshold_amount(conn, table.industry, min_eok=100)
        logger.info("    → 100억↑ 금액합계(전체 발주기관, 연간): %s 원",
                    "{:,.0f}".format(amt["krw"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
