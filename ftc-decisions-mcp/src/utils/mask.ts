/**
 * 인증값(OC) 마스킹 유틸리티.
 *
 * 로그, 오류 메시지, 응답 등 외부로 나갈 수 있는 모든 문자열에서
 * `OC=...` 파라미터 값을 가린다.
 */

const OC_PARAM_RE = /([?&](?:OC|oc)=)([^&\s]+)/g;

export function maskOc(value: string): string {
  return value.replace(OC_PARAM_RE, '$1***');
}

/**
 * URL에서 OC 파라미터를 완전히 제거한 뒤 문자열로 반환한다.
 * (로그/응답에 URL을 남길 때 사용)
 */
export function stripOcFromUrl(rawUrl: string): string {
  try {
    const url = new URL(rawUrl);
    url.searchParams.delete('OC');
    url.searchParams.delete('oc');
    return url.toString();
  } catch {
    return maskOc(rawUrl);
  }
}
