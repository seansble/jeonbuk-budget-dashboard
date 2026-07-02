"""[로컬 전용] 당초예산서 PDF → data/muju_stat.json (세부사업별 통계목 트리).

계층: 세부사업 > 편성목(목) > 통계목(세목) > 부기명(○) > 산출내역(―)+산출기초.
금액은 천원 단위(원문), 산출기초는 원단위 문자열 그대로.
★ 당초(본)예산이라 연 1회만 바뀜 → Actions(daily, QWGJK API)는 실행하지 않는다.
  로컬에서 이 스크립트로 muju_stat.json 을 생성해 커밋하면 index.html 이 로드한다.
좌표기반 파싱: x0로 과목 레벨, 별도 컬럼으로 예산액·산출기초 분리.
키 = '부서명\\u0001세부사업원본명' (동명 세부사업을 부서로 분리).
사용: python build/build_stat.py ["PDF경로"]
"""
import os
import re
import sys
import json
import collections

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
import fitz

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LAF = '4573000'                                    # 무주군(home)
DEFAULT_PDF = r'C:\Users\PC_1M\Downloads\FY2026 예산서(전북 무주군).pdf'
SEP = ''

# 명세서 상세표 범위 판별 헤더(반복됨)
DETAIL_HDR = '부서ㆍ정책ㆍ단위(회계)ㆍ세부사업ㆍ편성목'.replace('ㆍ', '')
FUND = ('국', '도', '군', '시', '기타', '도비', '국비', '군비')       # 재원구분(산출기초에서 제외)


def norm(s):
    return re.sub(r'\s+', '', s or '')


def to_int(s):
    s = (s or '').replace(',', '').strip()
    if not s:
        return None
    if s[0] in '△▲':
        return -int(s[1:] or 0)
    return int(s) if s.lstrip('-').isdigit() else None


def rows_of(page):
    """words → 논리행(y 근접 묶기), 각 행 = 정렬된 (x0, text)."""
    ws = page.get_text('words')
    ws.sort(key=lambda w: (round(w[1]), w[0]))
    rows, cur, cy = [], [], None
    for x0, y0, x1, y1, wd, *_ in ws:
        if cy is None or abs(y0 - cy) <= 4:
            cur.append((x0, wd)); cy = y0 if cy is None else cy
        else:
            rows.append(sorted(cur)); cur = [(x0, wd)]; cy = y0
    if cur:
        rows.append(sorted(cur))
    return rows


def cells(row):
    # 과목명 x0≤110 · 산출기초 x0 168~292(큰 금액은 텍스트가 길어 왼쪽으로 밀림) · 예산액 306~410
    left = [t for x, t in row if x < 168]
    # 산출기초(원단위) — 순수 숫자는 페이지번호 오염이라 제외(산출기초는 항상 원·×·단위 포함)
    basis = [t for x, t in row if 168 <= x < 292 and t not in FUND and not re.fullmatch(r'[\d,]+', t)]
    amt = [t for x, t in row if 306 <= x < 410]       # 예산액(천원)
    lx = min((x for x, t in row if x < 168), default=999)
    return left, basis, amt, lx


def parse(doc, pairs, disp, by_norm):
    biz = {}                                          # (dept,norm) → 트리
    dept = policy = unit = None
    cur = grp = stat = item = None
    for page in doc:
        if DETAIL_HDR not in page.get_text().replace('ㆍ', '').replace(' ', ''):
            continue
        for row in rows_of(page):
            left, basis, amt, lx = cells(row)
            ltxt = ''.join(left).strip()
            a = to_int(amt[0]) if amt else None
            if not ltxt and not basis:
                continue
            if ltxt.startswith('부서:'):
                dept = ltxt[3:]; continue         # cur 유지: 세부사업이 페이지 경계를 넘어 이어질 때 편성목 유실 방지
            if ltxt.startswith('정책:'):
                policy = ltxt[3:]; continue
            if ltxt.startswith('단위:'):
                unit = ltxt[3:]; continue

            m3 = re.match(r'^(\d{3})(\D.*)$', ltxt)
            m2 = re.match(r'^(\d{2})(\D.*)$', ltxt)
            if m3 and 78 <= lx < 84:                  # 편성목(목)
                if cur:
                    grp = {'code': m3.group(1), 'name': m3.group(2), 'amt': a, 'stats': []}
                    biz[cur]['groups'].append(grp); stat = item = None
            elif m2 and 84 <= lx < 89:                # 통계목(세목)
                if grp is not None:
                    stat = {'code': m2.group(1), 'name': m2.group(2), 'amt': a, 'items': []}
                    grp['stats'].append(stat); item = None
            elif ltxt.startswith('○'):                # 부기명
                if stat is not None:
                    item = {'name': ltxt[1:], 'amt': a, 'basis': list(basis), 'detail': []}
                    stat['items'].append(item)
            elif ltxt.startswith('―') or ltxt.startswith('ㅡ'):   # 산출내역 세부
                if item is not None:
                    item['detail'].append({'name': ltxt.lstrip('―ㅡ '), 'amt': a, 'basis': list(basis)})
            elif lx >= 88:                            # 이어짐/산출기초 전용행
                if item is not None:
                    tgt = item['detail'][-1] if item['detail'] else item
                    if '*' in ltxt or '×' in ltxt:    # 큰 금액이라 산출기초가 과목컬럼으로 흘러든 경우
                        tgt['basis'].append(ltxt)
                    elif ltxt:
                        tgt['name'] += ltxt
                    if a is not None and tgt['amt'] is None:
                        tgt['amt'] = a
                    if basis:
                        tgt['basis'].append(''.join(basis))
            elif '편성목' in ltxt or ltxt in ('예산액', '전년도', '비교증감'):
                continue                              # 페이지 상단 반복 헤더 → cur 유지(세부사업 이어짐)
            elif lx < 78:                             # 세부사업/합계 라벨
                nk = norm(ltxt)
                key = (dept, nk)
                cand = by_norm.get(nk)                 # 부서이동 폴백: 이름은 유니크한데 부서만 다름
                if key not in pairs and cand and len(cand) == 1:
                    key = next(iter(cand))            # API의 (정확한 부서, norm) 사용
                if key in pairs:
                    cur = key
                    if key not in biz:
                        biz[key] = {'name': disp[key], 'dept': key[0], 'policy': policy,
                                    'unit': unit, 'amt': a, 'groups': []}
                    grp = stat = item = None
                else:
                    cur = grp = stat = item = None
    return biz


def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF
    if not os.path.exists(pdf_path):
        print(f'! PDF 없음: {pdf_path}'); sys.exit(1)
    dept_map = json.load(open(os.path.join(ROOT, 'config', 'dept_map.json'), encoding='utf-8')).get(LAF, {})
    raw = json.load(open(os.path.join(ROOT, 'data', 'raw', f'{LAF}_2026.json'), encoding='utf-8'))['rows']

    pairs, disp, by_norm = set(), {}, {}               # (부서명, norm사업명)
    for x in raw:
        dnm = dept_map.get(x.get('dept_cd'), '')
        nk = norm(x['dbiz_nm'])
        key = (dnm, nk)
        pairs.add(key)
        disp.setdefault(key, x['dbiz_nm'])
        by_norm.setdefault(nk, set()).add(key)         # norm → 부서 집합(유니크면 부서이동 폴백)

    doc = fitz.open(pdf_path)
    biz = parse(doc, pairs, disp, by_norm)

    out = {}
    ok = bad = items = 0
    for (dnm, nk), v in biz.items():
        if not v['groups']:
            continue
        out[dnm + SEP + v['name']] = {'groups': v['groups'], 'amt': v['amt']}
        gs = sum(g['amt'] or 0 for g in v['groups'])
        if v['amt'] and abs(gs - v['amt']) <= 1:
            ok += 1
        elif v['amt']:
            bad += 1
        items += sum(len(s['items']) for g in v['groups'] for s in g['stats'])

    dst = os.path.join(ROOT, 'data', 'muju_stat.json')
    json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'✓ data/muju_stat.json — 세부사업 {len(out)}개 · 부기명 {items}개')
    print(f'  합계검증(편성목합=세부사업): OK {ok} / 불일치 {bad}')
    print(f'  파일크기: {os.path.getsize(dst) // 1024} KB')


if __name__ == '__main__':
    main()
