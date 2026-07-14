"""발전사 계약현황 odcloud API 조사 (남부발전 KOSPO · 동서발전 EWP).

공공데이터포털의 '파일데이터 자동변환 오픈API'(api.odcloud.kr)로 공개된
발전사 계약 체결 현황을 조사한다. 목적:
  1. 실제 응답 필드명 확정 (문서에는 한글 필드명만 서술되어 있음)
  2. 각 스냅샷 파일의 계약일자 커버리지(최소~최대) 확인
     — 연간 스냅샷이 누적분인지 해당 연도분인지 판별
  3. 100억↑ 공사 계약 건수 집계
  4. 표본 대조: '음성' 키워드 계약(동서발전 음성 천연가스 발전소 송전선로 등)
     존재 여부 — 나라장터 밖 발전사 자체계약 커버리지 갭 검증

인증키: data.go.kr 일반 인증키(G2B_API_KEY와 동일 계정) 재사용.
실행: monthly.yml probe 모드 (probe_script=probe_odcloud_contracts.py)
"""
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = "https://api.odcloud.kr/api"

DATASETS = {
    "kospo": {
        "name": "한국남부발전 계약 체결 현황",
        "endpoints": [
            ("2021", "/15095366/v1/uddi:50fea921-3458-4f38-a63e-f1a932d04a40"),
            ("2022", "/15095366/v1/uddi:35411ee3-53da-4d85-a688-ede46893dcdd"),
            ("2023", "/15095366/v1/uddi:25d4bcde-fe74-4e52-bffb-8dc8c04dcd13"),
            ("2024", "/15095366/v1/uddi:6e670b13-5f30-477c-ab9c-a5116b0392a1"),
            ("2025", "/15095366/v1/uddi:ca0adac0-6047-494c-886f-a2c917fbd49b"),
        ],
    },
    "ewp": {
        "name": "한국동서발전 공사·용역·물품 계약현황",
        "endpoints": [
            ("2025", "/15065323/v1/uddi:76402d29-9ed9-4ffe-9197-8dcc89147adc"),
        ],
    },
}

KEY = os.environ.get("G2B_API_KEY", "")
if not KEY:
    print("G2B_API_KEY 미설정 — 종료")
    sys.exit(1)
KEY = urllib.parse.unquote(KEY)  # 인코딩키/디코딩키 혼용 대비

MIN_PRICE = 10_000_000_000
KEYWORDS = ["음성", "송전선로", "천연가스"]


def fetch(path, page, per_page=1000):
    q = urllib.parse.urlencode(
        {"serviceKey": KEY, "page": page, "perPage": per_page,
         "returnType": "JSON"})
    url = f"{BASE}{path}?{q}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def to_num(v):
    try:
        return int(float(str(v).replace(",", "").replace("원", "").strip()))
    except (ValueError, TypeError):
        return None


def pick(row, *cands):
    """필드명 후보 중 실제 존재하는 첫 키의 값."""
    for k in row:
        for c in cands:
            if c in k:
                return row[k]
    return None


for src, meta in DATASETS.items():
    print(f"\n===== [{src}] {meta['name']} =====")
    for label, path in meta["endpoints"]:
        try:
            first = fetch(path, 1, per_page=3)
        except Exception as e:
            print(f"  [{label}] 요청 실패: {e}")
            continue
        total = first.get("totalCount")
        rows = first.get("data", [])
        print(f"  [{label}] totalCount={total}")
        if rows:
            print(f"  [{label}] 필드: {sorted(rows[0].keys())}")
            print(f"  [{label}] 샘플: {json.dumps(rows[0], ensure_ascii=False)[:300]}")

        # 전량 순회: 날짜 범위 / 100억↑ 공사 / 키워드 매칭
        dates, big, hits = [], [], []
        page, fetched = 1, 0
        while fetched < (total or 0):
            data = fetch(path, page).get("data", [])
            if not data:
                break
            for r in data:
                d = pick(r, "계약일")
                if d:
                    dates.append(str(d))
                name = str(pick(r, "계약명") or "")
                amt = to_num(pick(r, "계약금액"))
                exp = to_num(pick(r, "예정가격"))
                kind = str(pick(r, "조달유형", "구분") or "")
                biggest = max([v for v in (amt, exp) if v is not None], default=0)
                if biggest >= MIN_PRICE and ("공사" in kind or "공사" in name or kind == ""):
                    big.append((d, name[:60], amt, exp, kind))
                if any(k in name for k in KEYWORDS):
                    hits.append((d, name[:80], amt, exp, kind))
            fetched += len(data)
            page += 1
        if dates:
            print(f"  [{label}] 계약일자 범위: {min(dates)} ~ {max(dates)} (행 {fetched})")
        print(f"  [{label}] 100억↑(계약금액·예정가격 최대 기준, 공사 추정): {len(big)}건")
        for b in big[:15]:
            print(f"      {b}")
        print(f"  [{label}] 키워드({'/'.join(KEYWORDS)}) 매칭: {len(hits)}건")
        for h in hits[:15]:
            print(f"      {h}")
