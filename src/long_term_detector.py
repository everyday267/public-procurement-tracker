import re

# 단일 함수에서 모든 휴리스틱을 관리한다.
# 규칙 변경 시 이 파일만 수정하면 된다.
_PATTERNS = [
    r"장기계속",
    r"L/T",
    r"차수계약",
    r"장기\s*계속",
]
_COMPILED = [re.compile(p) for p in _PATTERNS]


def detect_long_term(text: str | None) -> bool:
    """공고명·계약방법 등 문자열에서 장기계속계약 여부를 판별한다."""
    if not text:
        return False
    return any(pat.search(text) for pat in _COMPILED)


def detect_long_term_from_raw(raw: dict, keys: list[str]) -> bool:
    """raw dict의 여러 키를 합쳐서 판별한다."""
    combined = " ".join(str(raw.get(k, "")) for k in keys)
    return detect_long_term(combined)
