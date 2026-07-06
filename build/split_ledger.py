"""muju_exec_raw.jsonl(원장 개별 지출 143k 줄) → 부서별 gz 슬라이스 + 인덱스.
MCP(muju_ledger)와 다른 프로젝트가 부서 단위로만 가볍게 받게 한다.

출력:
  data/ledger/_index.json     {부서명: {file, lines, spent}}  (+ asof)
  data/ledger/dNN.jsonl.gz     부서별 원장 줄(gzip, 각 줄=원본 dict)

파일명은 dNN(번호) — 한글 파일명 URL 인코딩 회피. 부서↔파일 매핑은 _index.json.
실행: python build/split_ledger.py [입력.jsonl]
"""
import sys, os, re, json, gzip, io, collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def split(src=None):
    src = src or os.path.join(ROOT, 'data', 'muju_exec_raw.jsonl')
    outdir = os.path.join(ROOT, 'data', 'ledger')
    os.makedirs(outdir, exist_ok=True)
    # 부서별로 줄 모으기
    by = collections.OrderedDict()
    asof = ''
    with open(src, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            by.setdefault(r.get('부서명', '?'), []).append(r)
            ymd = r.get('지급일자', '')
            if ymd > asof:
                asof = ymd
    # 부서명 정렬(결정적) → dNN 부여
    index = {}
    for i, dept in enumerate(sorted(by)):
        rows = by[dept]
        fn = f'd{i:02d}.jsonl.gz'
        raw = ('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows)).encode('utf-8')
        with gzip.open(os.path.join(outdir, fn), 'wb') as g:
            g.write(raw)
        index[dept] = {'file': fn, 'lines': len(rows),
                       'spent': sum(int(re.sub(r'[^0-9]', '', x.get('지출액', '') or '0') or 0) for x in rows)}
    meta = {'asof': asof, 'source': '무주군 재정정보공개 원장(개별 지출 줄)',
            'total_lines': sum(v['lines'] for v in index.values()), 'depts': index}
    json.dump(meta, open(os.path.join(outdir, '_index.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    tot_gz = sum(os.path.getsize(os.path.join(outdir, v['file'])) for v in index.values())
    print(f'✓ {len(index)}개 부서 / {meta["total_lines"]:,}줄 / gz 합계 {tot_gz//1024}KB → data/ledger/  (asof {asof})')
    for dept, v in sorted(index.items(), key=lambda x: -x[1]['lines'])[:5]:
        print(f'  {dept}: {v["lines"]:,}줄 / {v["spent"]//100000000}억')


if __name__ == '__main__':
    split(sys.argv[1] if len(sys.argv) > 1 else None)
