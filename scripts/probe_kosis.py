"""probe_kosis.py — KOSIS 건설업 통계 OpenAPI(getList) 조사.

개발 컨테이너는 kosis.kr egress가 정책 차단이므로 GitHub Actions 러너에서
실행한다 (monthly.yml workflow_dispatch, probe_script=probe_kosis.py).

확정 목표:
  1. 3개 표 각각 실호출 → 응답 배열 구조·필드명 확인, 원문 probe_output/ 저장
     (tests/fixtures/kosis/ 픽스처 후보)
  2. **분류축 매핑**: C1/C2/C3의 축이름(Cn_OBJ_NM)과 멤버(Cn_NM) 나열 →
     어느 축이 공사규모(금액구간)이고 어느 축이 발주기관인지, 100억↑ 구간이
     실제 존재하는지 확인 (검증 정렬의 핵심)
  3. itmId(계약액/계약건수 등) 의미 확인, 단위(UNIT_NM), 기간(PRD_SE/PRD_DE)
  4. prdSe=Y(연) vs M(월) 지원 여부 — 월 대조 가능성 판단

실행: KOSIS_API_KEY 시크릿 필요.
"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import requests

OUT = Path("probe_output")
OUT.mkdir(exist_ok=True)

BASE = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
TIMEOUT = 40

# 사용자 제공 3개 URL의 파라미터
TABLES = [
    ("gen",  "365", "DT_365001_A072", ["16365AAD2", "16365AAB6"], 3, "종합건설업"),
    ("spec", "366", "TX_36601_A089",  ["16366AAA0", "16366AAA1"], 3, "전문건설업"),
    ("elec", "370", "DT_370001_A010", ["T001", "16370AAD3"],      2, "전기공사업"),
]

OUTPUT_FIELDS = ("ORG_ID TBL_ID TBL_NM OBJ_ID OBJ_NM NM ITM_ID ITM_NM "
                 "UNIT_NM PRD_SE PRD_DE LST_CHN_DE")


def section(title):
    print("\n===== {} =====".format(title))


def build_params(api_key, org_id, tbl_id, itm_ids, levels, prd_se, periods):
    params = {
        "method": "getList", "apiKey": api_key, "orgId": org_id, "tblId": tbl_id,
        "itmId": " ".join(itm_ids), "format": "json", "jsonVD": "Y",
        "prdSe": prd_se, "newEstPrdCnt": periods, "outputFields": OUTPUT_FIELDS,
    }
    for i in range(1, 9):
        params["objL{}".format(i)] = "ALL" if i <= levels else ""
    return params


def call(api_key, org_id, tbl_id, itm_ids, levels, prd_se="Y", periods=1):
    params = build_params(api_key, org_id, tbl_id, itm_ids, levels, prd_se, periods)
    try:
        r = requests.get(BASE, params=params, timeout=TIMEOUT)
        return r, None
    except Exception as e:
        return None, "{}: {}".format(type(e).__name__, e)


def main():
    section("시크릿 확인")
    api_key = os.getenv("KOSIS_API_KEY", "")
    print("  KOSIS_API_KEY: {}".format(
        "설정됨 (len=%d)" % len(api_key) if api_key else "미설정"))
    if not api_key:
        print("  → 시크릿 등록 후 재실행 필요.")
        return 0

    for key, org_id, tbl_id, itm_ids, levels, name in TABLES:
        section("{} ({} / {})".format(name, org_id, tbl_id))

        # prdSe Y/M 둘 다 시도 (월별 지원 여부 확인)
        working = None
        for prd_se in ("Y", "M"):
            r, err = call(api_key, org_id, tbl_id, itm_ids, levels, prd_se, periods=1)
            if err:
                print("  prdSe={} → {}".format(prd_se, err))
                continue
            body = r.text.strip()
            is_arr = body[:1] == "["
            print("  prdSe={} → HTTP {} len={} array={}".format(
                prd_se, r.status_code, len(body), is_arr))
            if not is_arr:
                print("    (오류/비배열) head: {}".format(body[:200]))
                continue
            if working is None:
                working = (prd_se, r)

        if not working:
            print("  ★ 배열 응답 실패 — 파라미터/키 확인 필요")
            continue

        prd_se, r = working
        data = r.json()
        fname = "kosis_{}.json".format(key)
        (OUT / fname).write_text(json.dumps(data[:50], ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        print("  저장: probe_output/{} (총 {}행, prdSe={})".format(
            fname, len(data), prd_se))

        if data:
            print("  필드: {}".format(sorted(data[0].keys())))

        # 분류축 매핑: Cn_OBJ_NM → 멤버 목록
        dims = defaultdict(set)
        items = set()
        units = set()
        for row in data:
            for n in (1, 2, 3):
                obj = row.get("C{}_OBJ_NM".format(n))
                nm = row.get("C{}_NM".format(n))
                if obj:
                    dims[obj].add(nm)
            if row.get("ITM_NM"):
                items.add(row["ITM_NM"])
            if row.get("UNIT_NM"):
                units.add(row["UNIT_NM"])
        print("  항목(ITM_NM): {}".format(sorted(items)))
        print("  단위(UNIT_NM): {}".format(sorted(units)))
        for obj, members in dims.items():
            ms = sorted(m for m in members if m)
            scale = "  ← 공사규모(금액구간)?" if "규모" in obj else ""
            agency = "  ← 발주기관?" if "발주" in obj else ""
            month = "  ← 월별(분류축!)" if "월" in obj else ""
            # 공사규모 구간 전체를 출력한다 (100억↑ 구간 라벨 확인이 핵심)
            print("  분류축 [{}]{}{}{} ({}개): {}".format(
                obj, scale, agency, month, len(ms), ms))

    print("\n===== 완료 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
