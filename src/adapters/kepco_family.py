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

단, KEPCO 빅데이터 API는 '입찰공고'만 제공하고 계약 체결 현황은 주지 않는다.
남부발전(KOSPO)·동서발전(EWP)은 공공데이터포털 odcloud API로 계약이 별도 공개돼
있어 OdcloudContractsMixin으로 fetch_contracts를 보강한다(공고는 KEPCO 유지).
믹스인을 KEPCOAdapter보다 **앞에** 상속해 fetch_contracts를 오버라이드한다.
"""
from typing import Optional

from .kepco import KEPCOAdapter, COMPANY_IDS
from .genco_odcloud import OdcloudContractsMixin


class _GencoAdapter(KEPCOAdapter):
    """발전 자회사 공통 베이스 — company_id를 agency_codes[0] 기준으로 고정."""

    def __init__(self, service_key: Optional[str] = None, timeout: int = 30):
        super().__init__(service_key=service_key, timeout=timeout,
                         company_id=COMPANY_IDS[self.agency_codes[0]])


class KOWEPOAdapter(_GencoAdapter):
    """한국서부발전 (COM02)."""
    source = "kowepo"
    agency_codes = ["KOWEPO"]


class KOSPOAdapter(OdcloudContractsMixin, _GencoAdapter):
    """한국남부발전 (COM04). 공고=KEPCO 빅데이터, 계약=odcloud(믹스인)."""
    source = "kospo"
    agency_codes = ["KOSPO"]

    def _odcloud_is_construction(self, kind: str, name: str) -> bool:
        # KOSPO 구분은 '구매입찰정보/공사용역입찰정보'뿐 — 공사·용역이 한 코드에
        # 섞여 있어 계약명으로 공사만 분리한다(용역 제외). 조사 근거: probe #191.
        if "공사" not in kind:
            return False
        return "공사" in name and "용역" not in name


class KOMIPOAdapter(OdcloudContractsMixin, _GencoAdapter):
    """한국중부발전 (COM05). 공고=KEPCO 빅데이터, 계약=odcloud(믹스인, 15003748).

    ※ 계약 수집은 해당 odcloud 데이터셋(15003748)에 대한 활용신청이 승인돼야
      동작한다(미승인 시 401 → 계약 0건으로 우아하게 스킵, 공고는 정상).
    """
    source = "komipo"
    agency_codes = ["KOMIPO"]

    def _odcloud_is_construction(self, kind: str, name: str) -> bool:
        # 중부 입찰정보는 구분/조달방법 필드 의미가 KOSPO와 미세하게 다를 수 있어
        # 계약명 기준으로만 공사를 분리한다(공사 포함·용역 제외). 활용신청 승인 후
        # 실데이터로 재검증 예정.
        return "공사" in name and "용역" not in name


class KOENAdapter(_GencoAdapter):
    """한국남동발전 (COM06)."""
    source = "koen"
    agency_codes = ["KOEN"]


class EWPAdapter(OdcloudContractsMixin, _GencoAdapter):
    """한국동서발전 (COM08). 공고=KEPCO 빅데이터, 계약=odcloud(믹스인).

    EWP 구분은 공사/용역/물품이 직접 구분돼 믹스인 기본 판정(구분=='공사')을 쓴다.
    """
    source = "ewp"
    agency_codes = ["EWP"]


GENCO_ADAPTERS = {
    "kowepo": KOWEPOAdapter,
    "kospo":  KOSPOAdapter,
    "komipo": KOMIPOAdapter,
    "koen":   KOENAdapter,
    "ewp":    EWPAdapter,
}
