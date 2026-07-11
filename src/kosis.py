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
KOSIS_TABLES: Dict[str, KosisTable] = {
    "gen": KosisTable(
        key="gen", org_id="365", tbl_id="DT_365001_A072",
        itm_ids=("16365AAD2", "16365AAB6"), obj_levels=3, industry="종합",
        label="종합건설업 공사규모별 발주기관별 계약실적"),
    "spec": KosisTable(
        key="spec", org_id="366", tbl_id="TX_36601_A089",
        itm_ids=("16366AAA0", "16366AAA1"), obj_levels=3, industry="전문",
        label="전문건설업 공사규모별 발주기관별 계약실적"),
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
            raw = client.fetch_table(table, prd_se=prd_se, num_periods=num_periods)
        except (KosisError, requests.RequestException) as e:
            logger.warning("[KOSIS] %s 수집 실패: %s", key, e)
            continue
        rows = [client.normalize(r, table) for r in raw]
        n = upsert_kosis_stats(conn, rows)
        logger.info("[KOSIS] %s(%s) %d행 수집", key, table.industry, n)
        total += n
    return total


# ---------------------------------------------------------------------- #
# 저장 데이터 요약 (분류축 매핑 확인용)                                       #
# ---------------------------------------------------------------------- #

# 분류축 이름 판별 키워드 (표마다 축 순서가 달라 이름으로 찾는다)
_SCALE_KEYWORDS = ("규모", "금액", "도급")
_AGENCY_KEYWORDS = ("발주", "발주자", "발주기관", "주체")


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


def scale_agency_summary(conn, industry, itm_nm_like=None):
    # type: (object, str, Optional[str]) -> List[dict]
    """공사규모 × 발주기관 피벗. 축은 이름으로 자동 식별한다.

    반환: [{prd_de, itm_nm, unit_nm, scale, agency, dt}]. 축을 못 찾으면 빈 리스트
    (표 구조가 예상과 다름 → probe 필요 신호).
    """
    q = "SELECT * FROM kosis_stats WHERE industry = ?"
    args = [industry]
    if itm_nm_like:
        q += " AND itm_nm LIKE ?"
        args.append("%{}%".format(itm_nm_like))
    rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    out = []
    for r in rows:
        scale = agency = None
        for n in (1, 2, 3):
            obj = r.get("c{}_obj".format(n))
            nm = r.get("c{}_nm".format(n))
            if _match_dim(obj, _SCALE_KEYWORDS) and scale is None:
                scale = nm
            elif _match_dim(obj, _AGENCY_KEYWORDS) and agency is None:
                agency = nm
        if scale is None and agency is None:
            continue
        out.append({
            "prd_de": r["prd_de"], "itm_nm": r["itm_nm"], "unit_nm": r["unit_nm"],
            "scale": scale, "agency": agency, "dt": r["dt"],
        })
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
        if labels:
            logger.info("[%s] 분류축: %s", table.industry,
                        {obj: mem[:5] for obj, mem in labels.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
