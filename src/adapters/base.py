from abc import ABC, abstractmethod
from datetime import date
from typing import Iterator

CONSTRUCTION_MIN_PRICE = 10_000_000_000  # 100억 VAT 제외


class BaseProcurementAdapter(ABC):
    source: str
    agency_codes: list[str]

    @abstractmethod
    def fetch_notices(self, since: date, until: date) -> Iterator[dict]:
        """공고 목록 수집"""
        ...

    @abstractmethod
    def fetch_awards(self, since: date, until: date) -> Iterator[dict]:
        """낙찰결과 수집"""
        ...

    @abstractmethod
    def fetch_contracts(self, since: date, until: date) -> Iterator[dict]:
        """계약체결현황 수집"""
        ...

    @abstractmethod
    def normalize(self, raw: dict) -> dict:
        """원본 → 공통 스키마 변환"""
        ...

    def passes_filter(self, normalized: dict) -> bool:
        """공사 + 100억 이상 필터. None → unpriced 테이블 대상."""
        if normalized.get("work_type") != "공사":
            return False
        ep = normalized.get("estimated_price")
        if ep is None:
            return False
        return ep >= CONSTRUCTION_MIN_PRICE

    def is_unpriced(self, normalized: dict) -> bool:
        """공사이지만 추정가격 미공개인 경우."""
        return (
            normalized.get("work_type") == "공사"
            and normalized.get("estimated_price") is None
        )

    def health_check(self) -> bool:
        """어댑터 접속 가능 여부 확인"""
        return True
