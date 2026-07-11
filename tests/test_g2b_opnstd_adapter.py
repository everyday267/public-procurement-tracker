"""G2BOpnStdAdapter (PubDataOpnStdService) 유닛 테스트.

실제 API 호출 없이 normalize / 필터 / VAT 환산 / 낙찰·계약 필드를 검증.
"""
import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from src.adapters.g2b_opnstd import G2BOpnStdAdapter
from src.adapters.base import CONSTRUCTION_MIN_PRICE


@pytest.fixture
def adapter():
    return G2BOpnStdAdapter(api_key="test-key")


def test_encoding_key_is_normalized():
    """이미 URL 인코딩된 키(%2B, %2F)를 unquote 하여 이중 인코딩을 방지해야 함."""
    enc = "abc%2Bdef%2Fghi%3D%3D"
    a = G2BOpnStdAdapter(api_key=enc)
    assert a.api_key == "abc+def/ghi=="


def test_decoding_key_unchanged():
    """이미 디코딩된 원문 키는 unquote 해도 그대로여야 함 (멱등)."""
    dec = "abc+def/ghi=="
    a = G2BOpnStdAdapter(api_key=dec)
    assert a.api_key == "abc+def/ghi=="


# ------------------------------------------------------------------ #
# normalize 테스트 — 입찰공고 raw                                      #
# ------------------------------------------------------------------ #

def test_normalize_basic(adapter):
    """source, notice_id 필드가 g2b_opnstd 접두사를 써야 함."""
    raw = {
        "bidNtceNo": "R25BK00933743",
        "bidNtceOrd": "000",
        "bidNtceNm": "종합공사 장기계속 테스트",
        "bsnsDivNm": "공사",
        "bidprcPsblIndstrytyNm": "종합공사업",
        "cntrctCnclsMthdNm": "장기계속",
        "presmptPrce": "12000000000",
        "bidNtceSttusNm": "공고중",
        "bidNtceDate": "2025-07-01",
        "opengDate": "2025-07-08",
    }
    n = adapter.normalize(raw)
    assert n["source"] == "g2b_opnstd"
    assert n["notice_id"] == "g2b_opnstd:R25BK00933743:0"
    assert n["construction_type"] == "종합"
    assert n["is_long_term_continuing"] is True
    assert n["estimated_price"] == 12_000_000_000
    assert n["posted_at"] == "2025-07-01"
    assert n["bid_open_at"] == "2025-07-08"


def test_normalize_specialist(adapter):
    raw = {
        "bidNtceNo": "R25BK00999999",
        "bidNtceOrd": "0",
        "bidNtceNm": "전문공사 도장",
        "bsnsDivNm": "공사",
        "bidprcPsblIndstrytyNm": "전문공사업",
        "presmptPrce": "15000000000",
    }
    n = adapter.normalize(raw)
    assert n["construction_type"] == "전문"
    assert n["is_long_term_continuing"] is False


# ------------------------------------------------------------------ #
# normalize 테스트 — 낙찰정보 raw                                      #
# ------------------------------------------------------------------ #

def test_normalize_award_fields(adapter):
    """낙찰 raw에서 _award_* 필드가 정상 매핑되어야 함."""
    raw = {
        "bidNtceNo": "R25BK00925778",
        "bidNtceOrd": "000",
        "bsnsDivNm": "공사",
        "fnlSucsfCorpNm": "테스트건설()",
        "fnlSucsfCorpBizrno": "308-81-03521",
        "fnlSucsfAmt": "122845000",
        "fnlSucsfRt": "90.394",
        "presmptPrce": "13000000000",
    }
    n = adapter.normalize(raw)
    assert n["_award_corp"] == "테스트건설()"
    assert n["_award_corp_bizrno"] == "308-81-03521"
    assert n["_award_amt"] == 122_845_000
    assert n["_award_rate"] == "90.394"


# ------------------------------------------------------------------ #
# normalize 테스트 — 계약정보 raw                                      #
# ------------------------------------------------------------------ #

def test_normalize_contract_fields(adapter):
    """계약 raw에서 _contract_* 필드가 정상 매핑되어야 함."""
    raw = {
        "bidNtceNo": "R25BK00111111",
        "bidNtceOrd": "000",
        "bsnsDivNm": "공사",
        "cntrctAmt": "9800000000",
        "cntrctCnclsDate": "2025-08-01",
        "dmndInsttNm": "서울특별시 건설재난구",
        "presmptPrce": "10000000000",
    }
    n = adapter.normalize(raw)
    assert n["_contract_amt"] == 9_800_000_000
    assert n["_contract_date"] == "2025-08-01"
    assert n["_demand_inst"] == "서울특별시 건설재난구"


# ------------------------------------------------------------------ #
# passes_filter / is_unpriced 테스트                                  #
# ------------------------------------------------------------------ #

def test_passes_filter_above_threshold(adapter):
    n = {"work_type": "공사", "estimated_price": CONSTRUCTION_MIN_PRICE}
    assert adapter.passes_filter(n) is True


def test_passes_filter_below_threshold(adapter):
    n = {"work_type": "공사", "estimated_price": 5_000_000_000}
    assert adapter.passes_filter(n) is False


def test_passes_filter_non_construction(adapter):
    n = {"work_type": "용역", "estimated_price": 20_000_000_000}
    assert adapter.passes_filter(n) is False


def test_is_unpriced(adapter):
    n = {"work_type": "공사", "estimated_price": None}
    assert adapter.is_unpriced(n) is True


# ------------------------------------------------------------------ #
# VAT 환산 테스트                                                      #
# ------------------------------------------------------------------ #

def test_vat_exclusion(adapter):
    raw = {
        "bidNtceNo": "R25BK00000001",
        "bidNtceOrd": "0",
        "bidNtceNm": "종합공사 VAT 포함",
        "bsnsDivNm": "공사",
        "presmptPrce": "11000000000",
        "vatIncldYn": "Y",
    }
    n = adapter.normalize(raw)
    assert n["vat_included"] is True
    assert n["estimated_price"] == int(11_000_000_000 / 1.1)


# ------------------------------------------------------------------ #
# 날짜 분할 테스트 — 7일 단위 쫙크 분할                             #
# ------------------------------------------------------------------ #

def test_weekly_chunk_split(adapter):
    """쫙크 분할 로직: 14일 범위 → _request 2회 호출 확인."""
    with patch.object(adapter, "_request", return_value=[]) as mock_req:
        list(adapter.fetch_contracts(
            since=date(2026, 6, 1),
            until=date(2026, 6, 14),
        ))
        assert mock_req.call_count == 2


def test_weekly_chunk_exact_7days(adapter):
    """7일 정확히 → _request 1회만 호출."""
    with patch.object(adapter, "_request", return_value=[]) as mock_req:
        list(adapter.fetch_contracts(
            since=date(2026, 6, 1),
            until=date(2026, 6, 7),
        ))
        assert mock_req.call_count == 1


def test_award_weekly_chunk_split(adapter):
    """낙찰 쫙크 분할: 21일 범위 → _request 3회."""
    with patch.object(adapter, "_request", return_value=[]) as mock_req:
        list(adapter.fetch_awards(
            since=date(2026, 6, 1),
            until=date(2026, 6, 21),
        ))
        assert mock_req.call_count == 3


def test_notice_single_month_one_call(adapter):
    """한 달 이내 범위(월간 수집)는 공고 _request 1회만 호출."""
    with patch.object(adapter, "_request", return_value=[]) as mock_req:
        list(adapter.fetch_notices(since=date(2025, 3, 1), until=date(2025, 3, 31)))
    assert mock_req.call_count == 1


def test_notice_quarter_splits_by_month(adapter):
    """분기(1~3월) 범위 → 공고 1개월 제한 대응으로 달 단위 3회 분할."""
    with patch.object(adapter, "_request", return_value=[]) as mock_req:
        list(adapter.fetch_notices(since=date(2025, 1, 1), until=date(2025, 3, 31)))
    assert mock_req.call_count == 3
    p1 = mock_req.call_args_list[0].args[1]
    p2 = mock_req.call_args_list[1].args[1]
    assert p1["bidNtceBgnDt"] == "202501010000"
    assert p1["bidNtceEndDt"] == "202501312359"   # 1월 말일까지
    assert p2["bidNtceBgnDt"] == "202502010000"


def test_notice_full_year_twelve_calls(adapter):
    """연간 범위 → 12개월 분할."""
    with patch.object(adapter, "_request", return_value=[]) as mock_req:
        list(adapter.fetch_notices(since=date(2025, 1, 1), until=date(2025, 12, 31)))
    assert mock_req.call_count == 12


def test_notice_partial_month_range(adapter):
    """월 중간에 걸친 범위(2/15~4/10) → 2·3·4월 3회 분할, 경계 정확."""
    with patch.object(adapter, "_request", return_value=[]) as mock_req:
        list(adapter.fetch_notices(since=date(2025, 2, 15), until=date(2025, 4, 10)))
    assert mock_req.call_count == 3
    calls = mock_req.call_args_list
    assert calls[0].args[1]["bidNtceBgnDt"] == "202502150000"
    assert calls[0].args[1]["bidNtceEndDt"] == "202502282359"
    assert calls[2].args[1]["bidNtceBgnDt"] == "202504010000"
    assert calls[2].args[1]["bidNtceEndDt"] == "202504102359"


# ------------------------------------------------------------------ #
# bidNtceNo 스코프 조회 (probe 검증 + 폴백)                            #
# ------------------------------------------------------------------ #

def test_scoped_contracts_uses_bidntceno_when_filter_supported(adapter):
    """probe totalCount가 작으면 공고번호별 스코프 조회를 쓴다."""
    with patch.object(adapter, "_total_count", return_value=1) as probe, \
         patch.object(adapter, "_request", return_value=iter([])) as req:
        list(adapter.fetch_contracts_scoped(
            {"N1", "N2"}, date(2026, 6, 1), date(2026, 6, 30)))
    probe.assert_called_once()
    # 공고 2개 × 주 5개(6/1~6/30) = 10회, 모두 bidNtceNo 포함
    assert req.call_count == 10
    for call in req.call_args_list:
        assert "bidNtceNo" in call.args[1]


def test_scoped_contracts_falls_back_to_sweep_when_filter_ignored(adapter):
    """probe totalCount가 크면(필터 무시) 전국 스윕으로 폴백한다."""
    with patch.object(adapter, "_total_count", return_value=99999), \
         patch.object(adapter, "_request", return_value=iter([])) as req:
        list(adapter.fetch_contracts_scoped(
            {"N1"}, date(2026, 6, 1), date(2026, 6, 30)))
    # 폴백: 주 5개 스윕, bidNtceNo 없음
    assert req.call_count == 5
    for call in req.call_args_list:
        assert "bidNtceNo" not in call.args[1]


def test_scoped_empty_notice_set_fetches_nothing(adapter):
    """대상 공고가 없으면 아무 요청도 하지 않는다."""
    with patch.object(adapter, "_total_count") as probe, \
         patch.object(adapter, "_request") as req:
        result = list(adapter.fetch_contracts_scoped(
            set(), date(2026, 6, 1), date(2026, 6, 30)))
    assert result == []
    probe.assert_not_called()
    req.assert_not_called()


def test_scoped_probe_none_falls_back(adapter):
    """probe가 None(오류)이면 안전하게 전국 스윕으로 폴백한다."""
    with patch.object(adapter, "_total_count", return_value=None), \
         patch.object(adapter, "_request", return_value=iter([])) as req:
        list(adapter.fetch_awards_scoped(
            {"N1"}, date(2026, 6, 1), date(2026, 6, 7)))
    assert req.call_count == 1  # 1주 스윕
    assert "bidNtceNo" not in req.call_args_list[0].args[1]


# ------------------------------------------------------------------ #
# 계약 중심 모델 (체결일 기준 100억↑ 공사계약)                          #
# ------------------------------------------------------------------ #

def test_normalize_contract_rich(adapter):
    raw = {
        "bidNtceNo": "R25BK001", "cntrctNo": "C-1", "untyCntrctNo": "U-1",
        "cntrctNm": "○○도로 확장공사", "bsnsDivNm": "공사",
        "cntrctAmt": "25000000000", "ttalCntrctAmt": "26000000000",
        "cntrctCnclsDate": "2026-06-02", "cntrctCnclsMthdNm": "제한경쟁",
        "cntrctCnclsSttusNm": "계약완료", "lngtrmCtnuDivNm": "장기계속",
        "dmndInsttNm": "한국도로공사", "cntrctInsttNm": "조달청",
        "rprsntCorpNm": "대형건설", "rprsntCorpBizrno": "111-11-11111",
        "cntrctPrd": "2026-06-02~2028-06-01",
    }
    c = adapter.normalize_contract(raw)
    assert c["contract_price"] == 25_000_000_000
    assert c["total_contract_price"] == 26_000_000_000
    assert c["contracted_at"] == "2026-06-02"
    assert c["bsns_div"] == "공사"
    assert c["demand_inst"] == "한국도로공사"
    assert c["contractor_name"] == "대형건설"
    assert c["contractor_bizno"] == "111-11-11111"
    assert c["is_long_term"] == "장기계속"


def test_is_large_construction_contract(adapter):
    big = {"bsnsDivNm": "공사", "cntrctAmt": "25000000000"}
    small = {"bsnsDivNm": "공사", "cntrctAmt": "500000000"}
    service = {"bsnsDivNm": "용역", "cntrctAmt": "30000000000"}
    at_threshold = {"bsnsDivNm": "공사", "cntrctAmt": str(10_000_000_000)}
    assert adapter.is_large_construction_contract(big) is True
    assert adapter.is_large_construction_contract(small) is False
    assert adapter.is_large_construction_contract(service) is False
    assert adapter.is_large_construction_contract(at_threshold) is True
