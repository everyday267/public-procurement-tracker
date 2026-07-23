"""발전사 번들 파일 계약 믹스인 테스트 (Phase 2 Wave B — 남동발전 KOEN).

_parse_frame 파싱 / 공사 100억 필터 / normalize_contract 규약 검증 +
리포에 커밋된 실제 data/koen/*.xlsx 로 end-to-end 확인.
"""
import pytest
from datetime import date

import pandas as pd

from src.adapters.kepco_family import KOENAdapter
from src.adapters.genco_file import _parse_frame, FILE_DATASETS
from src.run_monthly import _is_target_contract


@pytest.fixture(autouse=True)
def _kepco_key(monkeypatch):
    monkeypatch.setenv("KEPCO_API_KEY", "kepco-key")  # 어댑터 생성용(공고)


def _frame(rows):
    """남동 파일 형태(제목행 + 헤더행 + 데이터)를 흉내낸 header=None DataFrame."""
    header = ["NO", "구분", "계약명", "계약일자", "계약금액\n(vat 포함)",
              "낙찰율\n(예산대비)", "업체명", "대표자", "담당부서"]
    data = [["1천만원 이상 공사, 용역, 물품 계약현황"] + [None] * 8,
            [None] * 9,
            header] + rows
    return pd.DataFrame(data)


# ── _parse_frame ────────────────────────────────────────────────────────────

def test_parse_frame_basic():
    rows = _parse_frame(_frame([
        [1, "공사", "영흥 저탄장 소방시설공사", "2022-11-08 00:00:00",
         14071225760, 87.7, "대한건설(주)", "홍길동", "사업소 영흥발전본부"],
        [2, "용역", "폐기물 처리 용역", "2022-01-02 00:00:00",
         359356560, 80.2, "그린(주)", "김철수", "영흥 경영지원처"],
    ]))
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["구분"] == "공사"
    assert r0["계약명"].startswith("영흥 저탄장")
    assert r0["계약일자"] == "2022-11-08"
    assert r0["계약금액"] == 14071225760
    assert r0["업체명"] == "대한건설(주)"
    assert r0["담당부서"] == "사업소 영흥발전본부"


def test_parse_frame_skips_blank_and_decimal_amount():
    rows = _parse_frame(_frame([
        [1, "물품", "TMS 교정용역", "2021-03-04 00:00:00", 13739661.1, 0.88, "더원", "노광섭", "삼천포"],
        [None, None, None, None, None, None, None, None, None],  # 빈 행
    ]))
    assert len(rows) == 1
    assert rows[0]["계약금액"] == 13739661  # 소수점 → 정수


# ── 필터 / 정규화 ───────────────────────────────────────────────────────────

def test_is_large_construction_contract():
    a = KOENAdapter()
    assert a.is_large_construction_contract({"구분": "공사", "계약금액": 12_625_032_000}) is True
    assert a.is_large_construction_contract({"구분": "공사", "계약금액": 9_000_000_000}) is False  # 90억
    assert a.is_large_construction_contract({"구분": "용역", "계약금액": 50_000_000_000}) is False
    assert a.is_large_construction_contract({"구분": "물품", "계약금액": 50_000_000_000}) is False


def test_normalize_contract():
    a = KOENAdapter()
    c = a.normalize_contract({
        "구분": "공사", "계약명": "분당복합 현대화사업 1Block 건설공사",
        "계약일자": "2024-10-24", "계약금액": 282_835_823_675,
        "업체명": "현대건설(주)", "담당부서": "사업소 분당발전본부",
    })
    assert c["source"] == "koen"
    assert c["bsns_div"] == "공사"
    assert c["contract_price"] == 282_835_823_675
    assert c["contracted_at"] == "2024-10-24"
    assert c["demand_inst"] == "한국남동발전 사업소 분당발전본부"
    assert c["contractor_name"] == "현대건설(주)"
    assert c["contract_no"] is None
    assert _is_target_contract(c) is True


def test_fetch_contracts_date_filter(monkeypatch):
    a = KOENAdapter()
    all_rows = [
        {"구분": "공사", "계약명": "A", "계약일자": "2022-05-31", "계약금액": 12_625_032_000},
        {"구분": "공사", "계약명": "B", "계약일자": "2024-10-24", "계약금액": 282_835_823_675},
    ]
    monkeypatch.setattr(a, "_iter_file_rows", lambda: iter(all_rows))
    got = list(a.fetch_contracts(date(2022, 1, 1), date(2022, 12, 31)))
    assert [r["계약명"] for r in got] == ["A"]


# ── 리포 번들 실파일 end-to-end ─────────────────────────────────────────────

def test_bundled_files_present():
    from pathlib import Path
    from src.adapters.genco_file import _REPO_ROOT
    d = _REPO_ROOT / FILE_DATASETS["koen"]
    assert d.is_dir()
    assert sorted(p.name for p in d.glob("*.xlsx")), "data/koen 에 xlsx가 있어야 함"


def test_bundled_files_yield_known_100억_construction():
    """실제 커밋된 남동 파일에서 알려진 100억↑ 공사 4건이 파이프라인 대상이 되는지."""
    a = KOENAdapter()
    raws = list(a.fetch_contracts(date(2020, 1, 1), date(2025, 12, 31)))
    targets = [a.normalize_contract(r) for r in raws
               if a.is_large_construction_contract(r)]
    targets = [c for c in targets if _is_target_contract(c)]
    names = sorted(c["contract_name"] for c in targets)
    # 스캔으로 확인된 4건 (2022×3, 2024×1)
    assert len(targets) == 4, names
    assert any("분당복합 현대화사업" in n for n in names)      # 2024, 2,828억
    assert any("강릉안인화력" in n and "보일러" in n for n in names)  # 2022, 493억
    prices = {c["contract_name"]: c["contract_price"] for c in targets}
    assert max(prices.values()) == 282_835_823_675
