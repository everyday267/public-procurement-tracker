import { describe, expect, it } from 'vitest';
import { maskOc, stripOcFromUrl } from '../src/utils/mask.js';

describe('maskOc', () => {
  it('OC 파라미터 값을 마스킹한다', () => {
    expect(maskOc('https://www.law.go.kr/DRF/lawSearch.do?OC=myid&target=ftc')).toBe(
      'https://www.law.go.kr/DRF/lawSearch.do?OC=***&target=ftc'
    );
  });
  it('소문자 oc도 마스킹한다', () => {
    expect(maskOc('?oc=secret&x=1')).toBe('?oc=***&x=1');
  });
});

describe('stripOcFromUrl', () => {
  it('URL에서 OC 파라미터를 제거한다', () => {
    const out = stripOcFromUrl('https://www.law.go.kr/DRF/lawService.do?OC=secret&ID=8111&type=JSON');
    expect(out).not.toContain('secret');
    expect(out).toContain('ID=8111');
  });
  it('상대 경로도 안전하게 마스킹한다', () => {
    const out = stripOcFromUrl('/DRF/lawService.do?OC=secret&ID=8111');
    expect(out).not.toContain('secret');
  });
});
