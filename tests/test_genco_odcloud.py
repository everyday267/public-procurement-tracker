"""발전사 계약 odcloud 믹스인 테스트 (Phase 2 Wave B).

KOSPO·EWP 계약 체결 현황(api.odcloud.kr) 수집·필터·정규화 검증.
공고는 KEPCO 빅데이터(test_kepco_family.py)에서 별도로 다룬다.
조사 근거 샘플: docs/PHASE2_WAVE_B_ODCLOUD_CONTRACTS.md (probe #191/#192).
"""
import pytest
from datetime import date
from unittest.mock import patch

from src.adapters.kepco_family import KOSPOAdapter, EWPAdapter, KOMIPOAdapter
from src.adapters.genco_odcloud import ODCLOUD_DATASETS
from src.run_monthly import _is_target_contract


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    # genco 어댑터는 공고용 KEPCO_API_KEY, 계약용 G2B_API_KEY를 각각 읽는다.
    monkeypatch.setenv("KEPCO_API_KEY", "kepco-key")
    monkeypatch.setenv("G2B_API_KEY", "g2b-key")


# 실제 odcloud 응답 형태(probe에서 확정)를 본뜬 raw 행 ──────────────────────

# EWP: 구분(공사/용역/물품) 직접 구분, 계약번호·계약업체·부가세 포함 금액.
EWP_BIG = {
    "계약금액(부가세 포함)": 105000000000, "계약명": "음성 천연가스 발전소 송전선로 건설 공사(종합심사낙찰제)",
    "계약방법": "제한경쟁", "계약번호": "C0082110130", "계약업체": "세안이엔씨 주식회사",
    "계약일자": "2021-04-12", "구분": "공사", "담당사업소": "상생조달처",
    "예정가격(부가세 포함)": 120000000000, "조달유형": "전자입찰",
}
EWP_SMALL_CONSTRUCTION = {  # 공사지만 90억
    "계약금액(부가세 포함)": 9000000000, "계약명": "당진화력 소방시설 공사",
    "계약번호": "C1", "구분": "공사", "담당사업소": "조달처", "계약일자": "2022-05-01",
}
EWP_SERVICE = {  # 용역 500억
    "계약금액(부가세 포함)": 50000000000, "계약명": "발전설비 비파괴검사 용역",
    "구분": "용역", "담당사업소": "조달처", "계약일자": "2023-03-20",
}
EWP_PRODUCT = {  # 물품 200억
    "계약금액(부가세 포함)": 20000000000, "계약명": "석탄 구매",
    "구분": "물품", "담당사업소": "조달처", "계약일자": "2023-04-01",
}

# KOSPO: 구분은 '구매입찰정보/공사용역입찰정보'뿐 → 계약명으로 공사 분리.
KOSPO_BIG = {
    "계약금액": "71161721400", "계약명": "2019∼2020년도 발전설비 경상정비공사",
    "계약일자": "2020-05-26", "구분": "공사용역입찰정보", "사업소": "하동발전본부", "일련번호": 12,
}
KOSPO_SERVICE = {  # 공사용역입찰정보지만 계약명이 '용역' → 공사 아님
    "계약금액": "60248169295", "계약명": "2021년 미화,경비,소방,시설관리 위탁용역",
    "계약일자": "2021-06-10", "구분": "공사용역입찰정보", "사업소": "본사", "일련번호": 30,
}
KOSPO_PURCHASE = {  # 구매입찰정보 → 공사 아님
    "계약금액": "75570840", "계약명": "하동 4호기 자재 구매계약",
    "계약일자": "2020-01-02", "구분": "구매입찰정보", "사업소": "하동발전본부", "일련번호": 1,
}


# ── 공사 판별 ──────────────────────────────────────────────────────────────

def test_ewp_construction_by_gubun():
    a = EWPAdapter()
    assert a._odcloud_is_construction("공사", EWP_BIG["계약명"]) is True
    assert a._odcloud_is_construction("용역", "무슨 공사 용역") is False
    assert a._odcloud_is_construction("물품", "공사자재") is False


def test_kospo_construction_by_name_heuristic():
    a = KOSPOAdapter()
    # 공사용역입찰정보 + 계약명에 '공사' 있고 '용역' 없음 → 공사
    assert a._odcloud_is_construction("공사용역입찰정보", "발전설비 경상정비공사") is True
    # '용역'이 섞이면 제외
    assert a._odcloud_is_construction("공사용역입찰정보", "시설관리 위탁용역") is False
    # 구매입찰정보는 공사 아님
    assert a._odcloud_is_construction("구매입찰정보", "자재 구매계약") is False


# ── 100억 사전 필터 ──────────────────────────────────────────────────────────

def test_is_large_construction_contract_ewp():
    a = EWPAdapter()
    assert a.is_large_construction_contract(EWP_BIG) is True
    assert a.is_large_construction_contract(EWP_SMALL_CONSTRUCTION) is False  # 90억
    assert a.is_large_construction_contract(EWP_SERVICE) is False            # 용역
    assert a.is_large_construction_contract(EWP_PRODUCT) is False            # 물품


def test_is_large_construction_contract_kospo():
    a = KOSPOAdapter()
    assert a.is_large_construction_contract(KOSPO_BIG) is True
    assert a.is_large_construction_contract(KOSPO_SERVICE) is False   # 용역
    assert a.is_large_construction_contract(KOSPO_PURCHASE) is False  # 구매


# ── 정규화 ──────────────────────────────────────────────────────────────────

def test_normalize_contract_ewp():
    c = EWPAdapter().normalize_contract(EWP_BIG)
    assert c["source"] == "ewp"
    assert c["contract_no"] == "C0082110130"
    assert c["contract_name"].startswith("음성 천연가스")
    assert c["bsns_div"] == "공사"
    assert c["contract_price"] == 105_000_000_000
    assert c["contracted_at"] == "2021-04-12"
    assert c["contract_method"] == "제한경쟁"
    assert c["demand_inst"] == "한국동서발전 상생조달처"
    assert c["contractor_name"] == "세안이엔씨 주식회사"
    assert c["notice_no"] is None
    assert _is_target_contract(c) is True


def test_normalize_contract_kospo():
    c = KOSPOAdapter().normalize_contract(KOSPO_BIG)
    assert c["source"] == "kospo"
    assert c["contract_price"] == 71_161_721_400
    assert c["contracted_at"] == "2020-05-26"
    assert c["demand_inst"] == "한국남부발전 하동발전본부"
    # KOSPO는 계약번호·계약업체·계약방법 미제공
    assert c["contract_no"] is None
    assert c["contractor_name"] is None
    assert c["contract_method"] is None
    assert _is_target_contract(c) is True


# ── fetch: 기간 필터 · 페이지네이션 · 키 부재 ────────────────────────────────

def test_fetch_contracts_filters_by_date():
    a = EWPAdapter()
    page = [EWP_BIG, EWP_SMALL_CONSTRUCTION, EWP_SERVICE, EWP_PRODUCT]
    # 2022년만 조회 → EWP_SMALL_CONSTRUCTION(2022-05-01)만 남아야 함
    with patch.object(a, "_odcloud_get", side_effect=[page, []]) as mock_get:
        rows = list(a.fetch_contracts(date(2022, 1, 1), date(2022, 12, 31)))
    assert rows == [EWP_SMALL_CONSTRUCTION]
    # 첫 페이지에서 4행 미만이라 조기 종료(2번째 호출 없음)
    assert mock_get.call_count == 1


def test_fetch_contracts_paginates():
    a = KOSPOAdapter()
    full = [dict(KOSPO_BIG, 일련번호=i) for i in range(1000)]  # 정확히 perPage
    tail = [KOSPO_BIG]
    with patch.object(a, "_odcloud_get", side_effect=[full, tail]) as mock_get:
        rows = list(a.fetch_contracts(date(2020, 1, 1), date(2020, 12, 31)))
    assert mock_get.call_count == 2                       # 첫 페이지 가득 → 2페이지 조회
    assert mock_get.call_args_list[1].args[2] == 2        # page 인자 == 2
    assert len(rows) == 1001


def test_fetch_contracts_skips_without_g2b_key(monkeypatch):
    monkeypatch.delenv("G2B_API_KEY", raising=False)
    a = EWPAdapter()
    with patch.object(a, "_odcloud_get") as mock_get:
        rows = list(a.fetch_contracts(date(2021, 1, 1), date(2021, 12, 31)))
    assert rows == []
    mock_get.assert_not_called()  # 키 없으면 네트워크 호출 자체를 안 함


def test_odcloud_path_picks_latest_year():
    # KOSPO는 2021~2025 등록 → 최신(2025) uddi를 써야 누적 최다.
    a = KOSPOAdapter()
    assert a._odcloud_path() == ODCLOUD_DATASETS["kospo"][2025]


def test_komipo_has_no_odcloud_contracts_but_keeps_notices():
    # 믹스인 미적용 genco(KOMIPO)는 odcloud 데이터셋도 없고 KEPCO의 빈
    # fetch_contracts를 그대로 유지 — 공고만 수집한다.
    assert "komipo" not in ODCLOUD_DATASETS
    a = KOMIPOAdapter()
    assert list(a.fetch_contracts(date(2021, 1, 1), date(2021, 12, 31))) == []
