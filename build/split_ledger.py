"""muju_exec_raw.jsonl(원장 개별 지출 143k 줄) → 부서별 비압축 JSON + 인덱스.
부서마다 URL 하나로 그 부서 원장 전체를 바로 읽게 한다(MCP 불필요, Codex/스크립트/브라우저 공용).

출력:
  data/ledger/<부서명>.json   부서별 원장(JSON 배열, 각 원소=원본 dict)  ← URL 하나로 fetch().json()
  data/ledger/_index.json     {asof, base_url, total_lines, depts:{부서:{file,url,lines,spent}}}

실행: python build/split_ledger.py [입력.jsonl]
"""
import sys, os, re, json, collections

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
    # 이전 산출물 정리(부서명 바뀌었을 때 잔재 방지)
    if os.path.isdir(outdir):
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
        fn = dept + '.json'                                 # 한글 파일명 = 읽기 좋은 URL(fetch 시 자동 인코딩)
        json.dump(rows, open(os.path.join(outdir, fn), 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'))
        index[dept] = {'file': fn, 'url': BASE_URL + urlquote(fn),
                       'lines': len(rows),
                       'spent': sum(_won(x.get('지출액')) for x in rows)}
    meta = {'asof': asof, 'source': '무주군 재정정보공개 원장(개별 지출 줄)',
            'base_url': BASE_URL, 'total_lines': sum(v['lines'] for v in index.values()),
            'depts': index}
    json.dump(meta, open(os.path.join(outdir, '_index.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    tot = sum(os.path.getsize(os.path.join(outdir, v['file'])) for v in index.values())
    print(f'✓ {len(index)}개 부서 / {meta["total_lines"]:,}줄 / 비압축 합계 {tot//1024//1024}MB → data/ledger/  (asof {asof})')
    for dept, v in sorted(index.items(), key=lambda x: -x[1]['lines'])[:5]:
        mb = os.path.getsize(os.path.join(outdir, v['file'])) / 1024 / 1024
        print(f'  {dept}: {v["lines"]:,}줄 / {v["spent"]//100000000}억 / {mb:.1f}MB')


def urlquote(s):
    import urllib.parse
    return urllib.parse.quote(s)


if __name__ == '__main__':
    split(sys.argv[1] if len(sys.argv) > 1 else None)
