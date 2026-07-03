"""kr_rail.py — 국가철도공단 어댑터

수집 경로:
  국가철도공단은 자체 OpenAPI가 없으며, 나라장터(G2B) OpenAPI에
  기관명(instNm) 필터를 적용하여 수집한다.
  ebid.kr.or.kr 자체 게재 건은 나라장터에 미등록될 수 있으므로
  커버리지 모니터링이 필요하다.

전제:
  G2BOpnStdAdapter 의 fetch_* 메서드를 그대로 상속하고,
  KR 기관코드 필터만 오버라이드한다.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

from .g2b_opnstd import G2BOpnStdAdapter

# 나라장터 기관코드 — 국가철도공단
# (조달청 기관코드 조회: https://www.g2b.go.kr 기관코드 검색)
KR_INST_CODE = "5270000"   # 국가철도공단 기관코드 (확인 필요 시 G2B 기관코드 조회)
KR_INST_NAME = "국가철도공단"


class KRRailAdapter(G2BOpnStdAdapter):
    """나라장터 G2B OpenAPI에서 국가철도공단 데이터만 수집하는 어댑터."""

    source = "kr_rail"
    agency_codes = ["KR_RAIL"]

    def _base_params(self) -> dict:
        """국가철도공단 기관코드 고정 파라미터."""
        return {
            "inqryDiv":  "1",          # 1: 기관별 조회
            "instCd":    KR_INST_CODE,
        }

    def fetch_notices(self, since: date, until: date) -> Iterator[dict]:
        """국가철도공단 입찰공고 수집.

        G2BOpnStdAdapter.fetch_notices 에 기관코드 필터를 추가한다.
        기관코드로 필터되지 않는 경우를 대비해 응답 결과를 instNm으로
        2차 필터링한다.
        """
        for row in super().fetch_notices(since, until):
            if self._is_kr_rail(row):
                yield row

    def fetch_awards(self, since: date, until: date) -> Iterator[dict]:
        """국가철도공단 낙찰결과 수집."""
        for row in super().fetch_awards(since, until):
            if self._is_kr_rail(row):
                yield row

    def fetch_contracts(self, since: date, until: date) -> Iterator[dict]:
        """국가철도공단 계약현황 수집."""
        for row in super().fetch_contracts(since, until):
            if self._is_kr_rail(row):
                yield row

    def _is_kr_rail(self, row: dict) -> bool:
        """기관명 또는 기관코드로 국가철도공단 여부 확인."""
        inst_nm = row.get("instNm", "") or row.get("dminsttNm", "")
        inst_cd = row.get("instCd", "") or row.get("dminsttCd", "")
        return KR_INST_NAME in inst_nm or inst_cd == KR_INST_CODE

    def normalize(self, raw: dict) -> dict:
        """G2B 정규화 결과에 source를 kr_rail로 덮어쓴다."""
        normalized = super().normalize(raw)
        normalized["source"] = self.source
        normalized["agency_code"] = "KR_RAIL"
        normalized["notice_id"] = normalized["notice_id"].replace(
            "g2b_opnstd:", "kr_rail:", 1
        )
        return normalized

    def health_check(self) -> bool:
        """G2B API 상태 확인."""
        return super().health_check()
