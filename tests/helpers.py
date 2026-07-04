"""tests/helpers.py — fixture 규격화 공통 헬퍼 (실행계획 §3.3, M2-0)

표준 배치: tests/fixtures/{source}/notices.json (또는 .xml)
어댑터 테스트는 load_fixture()로 로드해 파싱·normalize를 검증한다.
"""
import json
from pathlib import Path

FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def fixture_path(source: str, name: str) -> Path:
    return FIXTURE_ROOT / source / name


def load_fixture(source: str, name: str) -> str:
    """fixture 파일 원문(str) 로드."""
    return fixture_path(source, name).read_text(encoding="utf-8")


def load_json_fixture(source: str, name: str = "notices.json"):
    return json.loads(load_fixture(source, name))
