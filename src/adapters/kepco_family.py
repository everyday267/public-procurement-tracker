"""kepco_family.py — 발전 자회사 어댑터 5종 (Phase 2 Wave B)

한전 빅데이터플랫폼 "전자입찰 계약정보" API(kepco.py)는 companyId 파라미터로
발전 자회사 공고를 함께 제공한다 (기술문서 확정, 실행계획 §3.1 Wave B의
"공통 스크래퍼 베이스" 대신 OpenAPI 재사용으로 대체):

  COM02 한국서부발전(KOWEPO) / COM04 한국남부발전(KOSPO)
  COM05 한국중부발전(KOMIPO) / COM06 한국남동발전(KOEN)
  COM08 한국동서발전(EWP)

인증키는 한전과 동일한 KEPCO_API_KEY를 공유한다.
각 서브클래스는 source/agency_codes/companyId만 다르고 나머지(fetch·normalize·
필터·health_check)는 KEPCOAdapter를 그대로 상속한다.
"""
from typing import Optional

from .kepco import KEPCOAdapter, COMPANY_IDS


class _GencoAdapter(KEPCOAdapter):
    """발전 자회사 공통 베이스 — company_id를 agency_codes[0] 기준으로 고정."""

    def __init__(self, service_key: Optional[str] = None, timeout: int = 30):
        super().__init__(service_key=service_key, timeout=timeout,
                         company_id=COMPANY_IDS[self.agency_codes[0]])


class KOWEPOAdapter(_GencoAdapter):
    """한국서부발전 (COM02)."""
    source = "kowepo"
    agency_codes = ["KOWEPO"]


class KOSPOAdapter(_GencoAdapter):
    """한국남부발전 (COM04)."""
    source = "kospo"
    agency_codes = ["KOSPO"]


class KOMIPOAdapter(_GencoAdapter):
    """한국중부발전 (COM05)."""
    source = "komipo"
    agency_codes = ["KOMIPO"]


class KOENAdapter(_GencoAdapter):
    """한국남동발전 (COM06)."""
    source = "koen"
    agency_codes = ["KOEN"]


class EWPAdapter(_GencoAdapter):
    """한국동서발전 (COM08)."""
    source = "ewp"
    agency_codes = ["EWP"]


GENCO_ADAPTERS = {
    "kowepo": KOWEPOAdapter,
    "kospo":  KOSPOAdapter,
    "komipo": KOMIPOAdapter,
    "koen":   KOENAdapter,
    "ewp":    EWPAdapter,
}
