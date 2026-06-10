from typing import Any


def normalize_notice(raw: dict) -> dict:
    """어댑터별 raw dict를 공통 notices 스키마로 변환한다."""
    return {
        "notice_id":               raw.get("notice_id"),
        "source":                  raw.get("source"),
        "notice_no":               raw.get("notice_no"),
        "notice_rev":              raw.get("notice_rev", 0),
        "agency_code":             raw.get("agency_code"),
        "title":                   raw.get("title"),
        "work_type":               raw.get("work_type", "공사"),
        "construction_type":       raw.get("construction_type"),
        "is_long_term_continuing": bool(raw.get("is_long_term_continuing", False)),
        "bid_method":              raw.get("bid_method"),
        "estimated_price":         raw.get("estimated_price"),
        "vat_included":            bool(raw.get("vat_included", False)),
        "posted_at":               raw.get("posted_at"),
        "bid_open_at":             raw.get("bid_open_at"),
        "status":                  raw.get("status", "공고중"),
        "raw_payload":             raw,
        "source_hash":             raw.get("source_hash"),
        "collected_at":            raw.get("collected_at"),
    }
