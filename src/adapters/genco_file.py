"""genco_file.py — 발전사 계약 '번들 파일' 수집 믹스인 (Phase 2 Wave B)

일부 발전사는 계약 체결 현황을 odcloud 자동변환 API가 아니라 **엑셀 파일**로만
공개한다(예: 한국남동발전 '1천만원 이상 공사·용역·물품 계약현황' 연도별 xls).
data.go.kr 파일은 해외 IP(Actions 러너 포함) 차단이라 러너에서 직접 못 받으므로,
사용자가 내려받아 리포에 커밋한 파일을 읽어 계약을 수집한다.

번들 위치: data/<source>/*.xlsx (연도별). 신규 연도는 파일만 추가하면 된다.

남동발전 파일 형식(2021~2025 확인):
  - 상단에 제목행("1천만원 이상 …"), 헤더는 'NO·구분·계약명·계약일자·
    계약금액(vat 포함)·낙찰율(예산대비)·업체명·대표자·담당부서' 행.
  - 구분 = 공사/용역/물품(또는 구매) — EWP처럼 직접 구분되어 계약명 휴리스틱 불필요.
  - 계약금액은 VAT 포함(2021년은 소수점 포함) → 정수 변환.
  - 계약번호·예정가격 없음. 담당부서를 사업소로 사용.

정규화·필터 규약은 OdcloudContractsMixin과 동일(run_monthly 연동):
  is_large_construction_contract(공사+계약금액 100억↑) → normalize_contract.
"""
import glob
import logging
import os
from datetime import date
from pathlib import Path
from typing import Iterator, Optional

import pandas as pd

from .genco_odcloud import _to_int, _to_iso  # 공용 파싱 헬퍼 재사용

logger = logging.getLogger(__name__)

# 리포 루트 = src/adapters/genco_file.py → parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]

# source → 번들 파일 디렉터리(리포 상대). 연도별 *.xlsx 를 모두 읽는다.
FILE_DATASETS = {
    "koen": "data/koen",
}

# source → 발주기관 한글명 (demand_inst 구성용)
FILE_INST_NAME = {
    "koen": "한국남동발전",
}

MIN_CONTRACT_PRICE = 10_000_000_000  # 100억

# 헤더 탐지·추출 대상 컬럼(부분일치). 파일마다 줄바꿈/괄호 표기가 달라 부분일치로 흡수.
_COL_KIND = "구분"
_COL_NAME = "계약명"
_COL_DATE = "계약일자"
_COL_AMT = "계약금액"
_COL_VENDOR = "업체명"
_COL_DEPT = "담당부서"


def _find_col(cols, needle: str) -> Optional[str]:
    for c in cols:
        if needle in str(c):
            return c
    return None


def _parse_frame(raw: pd.DataFrame) -> list[dict]:
    """헤더 없이 읽은 원본 DataFrame → 계약 raw dict 목록.

    상단 제목행을 건너뛰고 '구분'+'계약명'이 있는 행을 헤더로 삼아 파싱한다.
    파일 형식이 조금씩 달라도(시트명·제목행 수) 견디도록 방어적으로 처리.
    """
    hdr_idx = None
    for i in range(min(15, len(raw))):
        vals = [str(x).strip() for x in raw.iloc[i].tolist()]
        if _COL_KIND in vals and any(_COL_NAME in v for v in vals):
            hdr_idx = i
            break
    if hdr_idx is None:
        return []
    header = [str(x).replace("\n", "").strip() for x in raw.iloc[hdr_idx].tolist()]
    body = raw.iloc[hdr_idx + 1:].copy()
    body.columns = header

    c_kind = _find_col(header, _COL_KIND)
    c_name = _find_col(header, _COL_NAME)
    c_date = _find_col(header, _COL_DATE)
    c_amt = _find_col(header, _COL_AMT)
    c_vendor = _find_col(header, _COL_VENDOR)
    c_dept = _find_col(header, _COL_DEPT)
    if not (c_kind and c_name and c_date and c_amt):
        return []

    rows = []
    for _, r in body.iterrows():
        name = r.get(c_name)
        if name is None or str(name).strip() in ("", "nan"):
            continue  # 빈/합계 행 스킵
        rows.append({
            "구분":     str(r.get(c_kind) or "").strip(),
            "계약명":   str(name).strip(),
            "계약일자": _to_iso(r.get(c_date)),
            "계약금액": _to_int(r.get(c_amt)),
            "업체명":   (str(r.get(c_vendor)).strip() if c_vendor else "") or None,
            "담당부서": (str(r.get(c_dept)).strip() if c_dept else "") or None,
        })
    return rows


class FileContractsMixin:
    """번들 엑셀 파일에서 계약을 fetch_contracts로 제공하는 믹스인.

    genco 어댑터(kepco_family.py)에서 KEPCOAdapter보다 앞에 상속해 계약 공백을
    메운다. 공고(fetch_notices)는 KEPCO 빅데이터 경로를 그대로 쓴다.
    """

    def _file_dir(self) -> Optional[Path]:
        rel = FILE_DATASETS.get(self.source)
        return (_REPO_ROOT / rel) if rel else None

    def _iter_file_rows(self) -> Iterator[dict]:
        d = self._file_dir()
        if not d or not d.is_dir():
            logger.info("[%s] 번들 계약 파일 디렉터리 없음(%s) — 계약 skip", self.source, d)
            return
        files = sorted(glob.glob(os.path.join(str(d), "*.xlsx")))
        if not files:
            logger.info("[%s] 번들 계약 파일 0개(%s) — 계약 skip", self.source, d)
            return
        for f in files:
            try:
                raw = pd.ExcelFile(f).parse(0, header=None)
            except Exception:
                logger.exception("[%s] 계약 파일 읽기 실패: %s", self.source, f)
                continue
            yield from _parse_frame(raw)

    # ── fetch ─────────────────────────────────────────────────────────────

    def fetch_contracts(self, since: date, until: date) -> Iterator[dict]:
        """번들 파일 전량을 읽고 계약일자 ∈ [since, until] 필터."""
        lo, hi = str(since), str(until)
        yielded, scanned = 0, 0
        for raw in self._iter_file_rows():
            scanned += 1
            iso = raw.get("계약일자")
            if iso and lo <= iso <= hi:
                yield raw
                yielded += 1
        logger.info("[%s] 파일 계약 %s~%s: %d건 (전체 %d행 스캔)",
                    self.source, since, until, yielded, scanned)

    # ── 필터 / 정규화 (run_monthly 연동 규약) ──────────────────────────────

    def is_large_construction_contract(self, raw: dict) -> bool:
        """구분=='공사' + 계약금액 100억↑."""
        if str(raw.get("구분") or "").strip() != "공사":
            return False
        amt = raw.get("계약금액")
        return amt is not None and amt >= MIN_CONTRACT_PRICE

    def normalize_contract(self, raw: dict) -> dict:
        inst = FILE_INST_NAME.get(self.source, self.source)
        dept = raw.get("담당부서")
        return {
            "source":           self.source,
            "notice_no":        None,
            "contract_no":      None,   # 파일에 계약번호 없음
            "contract_name":    raw.get("계약명"),
            "bsns_div":         "공사",
            "contract_price":   raw.get("계약금액"),
            "total_contract_price": None,
            "contracted_at":    raw.get("계약일자"),
            "contract_method":  None,
            "is_long_term":     None,
            "demand_inst":      f"{inst} {dept}".strip() if dept else inst,
            "contract_inst":    dept or inst,
            "contractor_name":  raw.get("업체명"),
            "contractor_bizno": None,
            "raw_payload":      raw,
        }
