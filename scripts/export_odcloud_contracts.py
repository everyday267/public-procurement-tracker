"""발전사(남부·동서발전) 계약 스냅샷에서 100억↑ 공사 계약을 CSV로 내보낸다.

probe_odcloud_contracts.py 조사 결과 확정된 구조 기반:
  - EWP: 2016-01~최신 누적, 구분(공사/용역/물품)·계약업체·계약번호 보유
  - KOSPO: 2020-01~최신 누적(최신 스냅샷 하나면 충분),
    '공사용역입찰정보'에 공사·용역 혼재 → 계약명 휴리스틱으로 공사만 분리

출력: 기존 계약 CSV와 동일 스키마를 '===== output/<file>.csv (N행) =====' 마커로
stdout에 전문 출력 → 개발 컨테이너에서 parse_logs.py로 회수한다.
금액은 원자료 그대로(부가세 포함 표기 필드) — G2B 계약금액과 동일 관행.
"""
import csv
import io
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = "https://api.odcloud.kr/api"
MIN_PRICE = 10_000_000_000

# 최신 누적 스냅샷만 사용
ENDPOINTS = {
    "kospo": ("/15095366/v1/uddi:ca0adac0-6047-494c-886f-a2c917fbd49b", "한국남부발전"),
    "ewp":   ("/15065323/v1/uddi:76402d29-9ed9-4ffe-9197-8dcc89147adc", "한국동서발전"),
}

COLS = ["source", "contracted_at", "demand_inst", "contract_name", "bsns_div",
        "contract_price", "total_contract_price", "is_long_term",
        "contract_method", "contractor_name", "contractor_bizno",
        "contract_no", "notice_no"]

KEY = urllib.parse.unquote(os.environ.get("G2B_API_KEY", ""))
if not KEY:
    print("G2B_API_KEY 미설정 — 종료")
    sys.exit(1)


def fetch_all(path):
    rows, page = [], 1
    while True:
        q = urllib.parse.urlencode({"serviceKey": KEY, "page": page,
                                    "perPage": 1000, "returnType": "JSON"})
        with urllib.request.urlopen(f"{BASE}{path}?{q}", timeout=60) as r:
            payload = json.loads(r.read().decode("utf-8"))
        data = payload.get("data", [])
        if not data:
            break
        rows.extend(data)
        if len(rows) >= (payload.get("totalCount") or 0):
            break
        page += 1
    return rows


def to_num(v):
    try:
        return int(float(str(v).replace(",", "").replace("원", "").strip()))
    except (ValueError, TypeError):
        return None


def pick(row, *cands):
    for k in row:
        for c in cands:
            if c in k:
                return row[k]
    return None


def is_construction(src, kind, name):
    if src == "ewp":
        return kind == "공사"
    # kospo: '공사용역입찰정보'에 공사·용역 혼재 → 계약명으로 분리
    if "공사" not in kind:
        return False
    return "공사" in name and "용역" not in name


out_rows = []
for src, (path, inst) in ENDPOINTS.items():
    rows = fetch_all(path)
    kept = 0
    for r in rows:
        name = str(pick(r, "계약명") or "")
        kind = str(pick(r, "조달유형") if src == "kospo" else pick(r, "구분") or "")
        if src == "kospo":
            kind = str(pick(r, "구분") or "")
        amt = to_num(pick(r, "계약금액"))
        exp = to_num(pick(r, "예정가격"))
        biggest = max([v for v in (amt, exp) if v is not None], default=0)
        if biggest < MIN_PRICE or not is_construction(src, kind, name):
            continue
        office = str(pick(r, "담당사업소", "사업소") or "")
        out_rows.append({
            "source": src,
            "contracted_at": str(pick(r, "계약일") or ""),
            "demand_inst": f"{inst} {office}".strip(),
            "contract_name": name,
            "bsns_div": "공사",
            "contract_price": amt or "",
            "total_contract_price": "",
            "is_long_term": "",
            "contract_method": str(pick(r, "계약방법") or ""),
            "contractor_name": str(pick(r, "계약업체") or ""),
            "contractor_bizno": "",
            "contract_no": str(pick(r, "계약번호") or ""),
            "notice_no": "",
        })
        kept += 1
    print(f"### [{src}] 전체 {len(rows)}행 중 100억↑ 공사 {kept}건", file=sys.stderr)

buf = io.StringIO()
w = csv.DictWriter(buf, fieldnames=COLS)
w.writeheader()
for r in sorted(out_rows, key=lambda x: x["contracted_at"]):
    w.writerow(r)
print(f"===== output/genco_contracts_odcloud.csv ({len(out_rows)}행) =====")
sys.stdout.write(buf.getvalue())
