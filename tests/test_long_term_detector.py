import pytest
from src.long_term_detector import detect_long_term, detect_long_term_from_raw


@pytest.mark.parametrize("text,expected", [
    ("장기계속공사", True),
    ("L/T 계약", True),
    ("차수계약 방식", True),
    ("일반 공사", False),
    (None, False),
    ("", False),
])
def test_detect_long_term(text, expected):
    assert detect_long_term(text) == expected


def test_detect_long_term_from_raw():
    raw = {"cntrctCnclsMthdNm": "장기계속", "bidNtceNm": "도로 공사"}
    assert detect_long_term_from_raw(raw, ["cntrctCnclsMthdNm", "bidNtceNm"]) is True

    raw2 = {"cntrctCnclsMthdNm": "일반계약", "bidNtceNm": "건물 신축"}
    assert detect_long_term_from_raw(raw2, ["cntrctCnclsMthdNm", "bidNtceNm"]) is False
