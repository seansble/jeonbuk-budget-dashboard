"""[로컬 전용] 행안부 지방세통계연감 hwpx → data/jeonbuk_tax.json (연도별 14시군 세목별 징수).
Downloads의 '행정안전부_지방세통계연감_YYYYMMDD.hwpx' 전부 파싱 → 연도별 누적.
★ 통계연감은 연1회 발간(발간연도 = 실적연도+1) → 로컬에서 파싱해 커밋, Actions는 결과만 사용.
세목: 취득세(부동산 거래)·재산세(부동산 보유)·지방소득세(소득) 등. 5개년 파일 모으면 추이.
사용: python build/build_tax.py  (Downloads 파일 자동 탐색)
"""
import os
import re
import sys
import glob
import json
import zipfile

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DL = os.path.join(os.path.expanduser('~'), 'Downloads')

DOSE = ['취득세', '등록면허세', '레저세', '지방소비세', '지역자원시설세', '지방교육세', '과년도수입']
SGS = ['지방소비세', '주민세', '지방소득세', '재산세', '자동차세', '담배소비세', '도축세', '도시계획세', '과년도수입']
SI = ['전주', '군산', '익산', '정읍', '남원', '김제']          # 시(도세+시세)
GUN = ['완주', '진안', '무주', '장수', '임실', '순창', '고창', '부안']   # 군(도세+군세)
# laf_cd 매핑(regions.json 과 동일 체계) — 프론트 결합용
LAF = {'전주': '4511000', '군산': '4513000', '익산': '4514000', '정읍': '4515000', '남원': '4516000',
       '김제': '4512000', '완주': '4571000', '진안': '4572000', '무주': '4573000', '장수': '4574000',
       '임실': '4575000', '순창': '4576000', '고창': '4577000', '부안': '4578000'}


def load_text(path):
    """hwpx(zip)·pdf(fitz)·hwp(olefile) → 공백 정규화 텍스트. 형식 달라도 표 구조는 동일."""
    lo = path.lower()
    if lo.endswith('.hwpx'):
        z = zipfile.ZipFile(path)
        full = ''
        for n in sorted(z.namelist()):
            if 'section' in n.lower() and n.endswith('.xml'):
                full += ' '.join(re.findall(r'<hp:t[^>]*>([^<]*)</hp:t>', z.read(n).decode('utf-8', 'ignore'))) + ' '
    elif lo.endswith('.pdf'):
        import fitz
        d = fitz.open(path)
        full = ' '.join(d[i].get_text() for i in range(d.page_count))
    elif lo.endswith('.hwp'):
        try:
            import olefile
            f = olefile.OleFileIO(path)
            import zlib
            parts = []
            for s in f.listdir():
                if s and s[0] == 'BodyText':
                    data = f.openstream(s).read()
                    try:
                        data = zlib.decompress(data, -15)
                    except Exception:
                        pass
                    parts.append(data.decode('utf-16le', 'ignore'))
            full = ' '.join(parts)
        except Exception as e:
            print(f'  ! hwp 파싱 실패({os.path.basename(path)}): {e} → 스킵'); return None
    else:
        return None
    return re.sub(r'\s+', ' ', full)


def parse_table(full, tag, moks):
    """세목명 나열 뒤 '부과N 징수N ...' 순 → 징수액 dict."""
    i = full.find(tag)
    if i < 0:
        return {}
    seg = full[i:i + 1800]
    k = seg.find('Previous Years')          # 영문 세목명 끝 → 값 시작
    if k < 0:
        return {}
    vals = []
    for t in seg[k + len('Previous Years'):].split():
        t = t.replace('　', '')
        if re.match(r'^[\d,]+$', t):
            vals.append(int(t.replace(',', '')))
        elif t == '-':
            vals.append(0)
        else:
            break
    n = len(moks)
    return dict(zip(moks, vals[n:2 * n]))   # 부과N 다음 = 징수N


def year_of(fn):
    """파일명 → 실적연도. '(YYYY년 실적)' 우선, 없으면 발간연도-1."""
    m = re.search(r'\((\d{4})\s*년\s*실적\)', fn)
    if m:
        return m.group(1)
    m = re.search(r'(20\d{2})', fn)
    return str(int(m.group(1)) - 1) if m else '?'


def parse_file(path):
    full = load_text(path)
    if not full:
        return None, {}
    year = year_of(os.path.basename(path))
    out = {}
    for s in SI:
        dose = parse_table(full, s + '시(도세)', DOSE)
        sise = parse_table(full, s + '시(시세)', SGS)
        rec = {**dose, **sise}
        if rec:
            out[LAF[s]] = {'name': s + '시', **rec}
    for s in GUN:
        dose = parse_table(full, s + '군(도세)', DOSE)
        gunse = parse_table(full, s + '군(군세)', SGS)
        rec = {**dose, **gunse}
        if rec:
            out[LAF[s]] = {'name': s + '군', **rec}
    return year, out


def main():
    files = sorted(sum((glob.glob(os.path.join(DL, f'*지방세통계연감*.{e}')) for e in ('hwpx', 'pdf', 'hwp')), []))
    if not files:
        print(f'! 통계연감 파일 없음: {DL}'); sys.exit(1)
    tax = {}
    for f in files:
        year, data = parse_file(f)
        if data:
            tax[year] = data
        print(f'  {os.path.basename(f)} → 실적 {year}년, {len(data)}개 시군')
    # ★ 미발간 연감 = 직전 실적 재수록 → 인접 연도 완전동일이면 뒤 연도 버림(성장 0% 왜곡 방지)
    yrs = sorted(tax)
    for a, b in zip(yrs, yrs[1:]):
        if tax.get(a) == tax.get(b):
            print(f'  ! {b}년 = {a}년과 완전동일(미발간 중복) → 제외')
            tax.pop(b)
    dst = os.path.join(ROOT, 'data', 'jeonbuk_tax.json')
    json.dump(tax, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'✓ data/jeonbuk_tax.json — 연도 {sorted(tax)} · {os.path.getsize(dst) // 1024}KB')


if __name__ == '__main__':
    main()
