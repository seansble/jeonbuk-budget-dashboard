"""[로컬 전용] 무주 1회 추경 세출예산 사업명세서 → muju_stat.json 을 현액(본예산+추경)으로 업그레이드.

muju.go.kr 2026_1budget 부서별 명세서(032~074) 다운 → 파싱(예산액=현액 컬럼) → build_stat 이 만든
당초 muju_stat.json 위에 통계목 단위로 덮어쓰기(추경에 나온 통계목만 현액으로 교체, 안 나온 건 유지, 신설은 추가).
  실행순서: build_stat.py(당초) → build_chugyeong.py(현액)
  ★ 추경은 '증감 발생 사업/통계목만' 수록 → 통계목 레벨 병합이 정확(예산액=현액이라 산수 불필요).
사용: python build/build_chugyeong.py
"""
import os
import sys
import ssl
import json
import urllib.request

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
import fitz

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import build_stat as BS                                  # parse/norm 재사용

LAF = '4573000'
BASE = 'https://www.muju.go.kr/download/yesan/down/2026_1budget/'
NUMS = range(32, 75)                                     # 부서별 세출 명세서(세입/특별 섞여도 세출헤더로 필터됨)


def download_merged():
    ctx = ssl.create_default_context(); ctx.set_ciphers('DEFAULT@SECLEVEL=1')
    ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    merged = fitz.open(); got = 0
    for n in NUMS:
        try:
            req = urllib.request.Request(f'{BASE}{n:03d}.pdf', headers={'User-Agent': 'Mozilla/5.0'})
            data = urllib.request.urlopen(req, context=ctx, timeout=60).read()
            d = fitz.open('pdf', data); merged.insert_pdf(d); d.close(); got += 1
        except Exception:
            pass
    print(f'  추경 명세서 {got}개 다운·병합 ({merged.page_count}쪽)')
    return merged


def find_group(base, cg):
    for g in base['groups']:
        if g.get('code') == cg.get('code') and BS.norm(g['name']) == BS.norm(cg['name']):
            return g
    for g in base['groups']:                             # 코드 다르면 이름만
        if BS.norm(g['name']) == BS.norm(cg['name']):
            return g
    return None


def find_stat(grp, cs):
    for s in grp['stats']:
        if s.get('code') == cs.get('code') and BS.norm(s['name']) == BS.norm(cs['name']):
            return s
    for s in grp['stats']:
        if BS.norm(s['name']) == BS.norm(cs['name']):
            return s
    return None


def merge(stat, chug):
    """chug(추경 현액 트리)를 stat(당초) 위에 통계목 단위로 덮어쓰기. 반환: 교체/추가/신설 카운트."""
    rep = add = newbiz = 0
    for (dept, nk), cv in chug.items():
        if not cv['groups']:
            continue
        key = dept + BS.SEP + cv['name']
        if key not in stat:                              # 추경 신설 사업 → 통째 추가
            stat[key] = {'groups': cv['groups'], 'amt': cv['amt'], 'chug': 2}   # chug=2 신설
            newbiz += 1
            continue
        base = stat[key]
        base['chug'] = 1                                 # chug=1 추경으로 변경(증감)
        for cg in cv['groups']:
            bg = find_group(base, cg)
            if bg is None:
                base['groups'].append(cg); add += len(cg['stats']); continue
            for cs in cg['stats']:
                bs = find_stat(bg, cs)
                if bs is not None:                       # 통계목 현액으로 교체(부기명·산출기초도 추경본으로)
                    bs['amt'] = cs['amt']; bs['items'] = cs['items']; rep += 1
                else:
                    bg['stats'].append(cs); add += 1
        # 현액 = 통계목 합으로 재계산(안 나온 통계목=당초 유지 포함)
        for g in base['groups']:
            g['amt'] = sum((s['amt'] or 0) for s in g['stats'])
        base['amt'] = sum((g['amt'] or 0) for g in base['groups'])
    return rep, add, newbiz


def main():
    dst = os.path.join(ROOT, 'data', 'muju_stat.json')
    src = os.path.join(ROOT, 'data', 'muju_stat_dangcho.json')   # 당초 원본(재실행 안전 = 항상 여기서 시작)
    if not os.path.exists(src):
        if not os.path.exists(dst):
            print('! muju_stat 없음 — 먼저 build_stat.py 실행'); sys.exit(1)
        json.dump(json.load(open(dst, encoding='utf-8')), open(src, 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'))   # 현재 muju_stat(당초)을 백업
        print('  당초 백업 생성: muju_stat_dangcho.json')
    stat = json.load(open(src, encoding='utf-8'))
    n0 = len(stat)

    # 추경 파싱용 pairs (QWGJK 사업명 매칭) — build_stat.main 과 동일
    dept_map = json.load(open(os.path.join(ROOT, 'config', 'dept_map.json'), encoding='utf-8')).get(LAF, {})
    raw = json.load(open(os.path.join(ROOT, 'data', 'raw', f'{LAF}_2026.json'), encoding='utf-8'))
    rows = raw if isinstance(raw, list) else raw['rows']
    pairs, disp, by_norm = set(), {}, {}
    for x in rows:
        dnm = dept_map.get(x.get('dept_cd'), ''); nk = BS.norm(x['dbiz_nm']); k = (dnm, nk)
        pairs.add(k); disp.setdefault(k, x['dbiz_nm']); by_norm.setdefault(nk, set()).add(k)

    doc = download_merged()
    chug = BS.parse(doc, pairs, disp, by_norm)
    chug = {k: v for k, v in chug.items() if v['groups']}
    print(f'  추경 세부사업(증감분) {len(chug)}개 파싱')

    rep, add, newbiz = merge(stat, chug)
    json.dump(stat, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'✓ muju_stat.json 현액화 — 통계목 교체 {rep} · 신설통계목 {add} · 신설사업 {newbiz}')
    print(f'  세부사업 {n0} → {len(stat)} · 크기 {os.path.getsize(dst) // 1024} KB')


if __name__ == '__main__':
    main()
