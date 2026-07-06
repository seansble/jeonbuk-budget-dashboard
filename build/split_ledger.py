"""muju_exec_raw.jsonl(원장 개별 지출 143k 줄) → 부서별 gz 슬라이스 + 인덱스.
부서마다 URL 하나로 그 부서 원장 전체를 받게 한다(MCP 불필요, Codex/스크립트/브라우저 공용).
gz = 매일 갱신·커밋해도 git 히스토리 거의 안 커짐(사회복지과 21MB→~360KB). 읽기: fetch/curl 후 gunzip.

출력:
  data/ledger/<부서명>.jsonl.gz   부서별 원장(gzip JSONL, 각 줄=원본 dict)
  data/ledger/_index.json         {asof, base_url, total_lines, depts:{부서:{file,url,lines,spent}}}

실행: python build/split_ledger.py [입력.jsonl]
"""
import sys, os, re, json, gzip, collections, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE_URL = "https://raw.githubusercontent.com/seansble/jeonbuk-budget-dashboard/main/data/ledger/"
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def _won(x):
    return int(re.sub(r'[^0-9]', '', x or '0') or 0)


def split(src=None):
    src = src or os.path.join(ROOT, 'data', 'muju_exec_raw.jsonl')
    outdir = os.path.join(ROOT, 'data', 'ledger')
    if os.path.isdir(outdir):                              # 이전 산출물 정리(부서명 변동·형식 변경 잔재 방지)
        for f in os.listdir(outdir):
            if f.endswith('.json') or f.endswith('.jsonl.gz'):
                os.remove(os.path.join(outdir, f))
    os.makedirs(outdir, exist_ok=True)

    by = collections.OrderedDict()
    asof = ''
    with open(src, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            by.setdefault(r.get('부서명', '?'), []).append(r)
            if r.get('지급일자', '') > asof:
                asof = r.get('지급일자', '')

    index = {}
    for dept in sorted(by):
        rows = by[dept]
        fn = dept + '.jsonl.gz'
        raw = ('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows)).encode('utf-8')
        with gzip.open(os.path.join(outdir, fn), 'wb') as g:
            g.write(raw)
        index[dept] = {'file': fn, 'url': BASE_URL + urllib.parse.quote(fn),
                       'lines': len(rows), 'spent': sum(_won(x.get('지출액')) for x in rows)}
    meta = {'asof': asof, 'source': '무주군 재정정보공개 원장(개별 지출 줄, gzip JSONL)',
            'base_url': BASE_URL, 'read': 'fetch/curl 후 gunzip → 줄마다 JSON',
            'total_lines': sum(v['lines'] for v in index.values()), 'depts': index}
    json.dump(meta, open(os.path.join(outdir, '_index.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    tot = sum(os.path.getsize(os.path.join(outdir, v['file'])) for v in index.values())
    print(f'✓ {len(index)}개 부서 / {meta["total_lines"]:,}줄 / gz 합계 {tot//1024}KB → data/ledger/  (asof {asof})')
    for dept, v in sorted(index.items(), key=lambda x: -x[1]['lines'])[:5]:
        kb = os.path.getsize(os.path.join(outdir, v['file'])) // 1024
        print(f'  {dept}: {v["lines"]:,}줄 / {v["spent"]//100000000}억 / gz {kb}KB')


if __name__ == '__main__':
    split(sys.argv[1] if len(sys.argv) > 1 else None)
