"""무주 재정공개 원장(expenditurelist) → data/muju_exec_biz.json 리치 아카이브.
세부사업×통계목 단위로 집행액·건수·월별·적요(뭐에 썼나) 수집. 재수집 비싸므로 한 번에 최대 수집.
  key = '부서명\\x01세부사업명' (muju_stat 키와 동일)
  통계목별 = {s:집행합(원), n:건수, m:{월:집행}, d:[[적요,금액],... top3]}

모드:
  python build/muju_exec.py backfill            # 연초~오늘 전체 (~100분, 로컬)
  python build/muju_exec.py inc [YYYYMMDD]      # 하루치 증분 → 기존 아카이브에 병합 (기본=어제, Actions용 ~2분)
"""
import sys, os, re, ssl, time, json
import urllib.request, urllib.parse
from datetime import date, timedelta

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, 'data', 'muju_exec_biz.json')
BASE = 'https://copen.muju.go.kr/ebudget/expenditurebudget/expenditurelist'
LABELS = ['번호', '회계구분', '부서명', '세부사업명', '통계목', '지급일자', '사업개요(적요)', '지출액', '지급명령번호']
CTX = ssl.create_default_context(); CTX.set_ciphers('DEFAULT@SECLEVEL=1')   # 무주 서버 = 구형 TLS
TOPD = 3          # 통계목별 대표 적요 개수
DLEN = 60         # 적요 축약 길이


def fetch(params, retries=4):
    url = BASE + '?' + urllib.parse.urlencode(params, encoding='utf-8')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    last = None
    for i in range(retries):
        try:
            return urllib.request.urlopen(req, context=CTX, timeout=40).read().decode('utf-8', 'ignore')
        except Exception as e:                       # 타임아웃/연결끊김 = 일시적 → 백오프 후 재시도
            last = e
            if i < retries - 1:
                time.sleep(2 * (i + 1) + 1)
    raise last


def parse_rows(h):
    out = []
    for tb in re.findall(r'<tbody[^>]*>(.*?)</tbody>', h, re.S):
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tb, re.S):
            cells = [re.sub(r'<[^>]*>', '', c).replace('&nbsp;', ' ').strip()
                     for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)]
            row = {}
            for c in cells:
                for lb in LABELS:
                    if c.startswith(lb):
                        row[lb] = c[len(lb):].strip(); break
            if row.get('통계목') and row.get('지출액'):
                out.append(row)
    return out


def add(archive, r):
    """원장 1건 → 아카이브 누적. 적요는 텍스트별 합산 후 top3 유지."""
    amt = int(re.sub(r'[^0-9]', '', r['지출액']) or 0)
    k = r.get('부서명', '?') + '\x01' + r.get('세부사업명', '?')
    mok = r['통계목']
    mm = (r.get('지급일자', '') or '')[4:6] or '00'
    node = archive.setdefault(k, {}).setdefault(mok, {'s': 0, 'n': 0, 'm': {}, '_d': {}})
    node['s'] += amt
    node['n'] += 1
    node['m'][mm] = node['m'].get(mm, 0) + amt
    desc = (r.get('사업개요(적요)', '') or '').strip()[:DLEN]
    if desc:
        node['_d'][desc] = node['_d'].get(desc, 0) + amt


def _out_node(nd):
    """아카이브 노드(_d dict 보유) → 저장 노드(d top3 리스트). 원본 불변."""
    top = sorted(nd['_d'].items(), key=lambda x: -x[1])[:TOPD]
    return {'s': nd['s'], 'n': nd['n'], 'm': nd['m'], 'd': [[t, a] for t, a in top]}


def rehydrate(archive):
    """저장본(d 리스트) → 내부 _d dict 복원 (증분 병합용)."""
    for moks in archive.values():
        for node in moks.values():
            node['_d'] = {t: a for t, a in node.get('d', [])}
            node.pop('d', None)
    return archive


def scan_range(day1, day2, archive, tag='', sleep=0.08):
    """기간 원장 전체 페이지 순회 → archive 누적. 반환 건수."""
    p, prev_first, n = 1, None, 0
    while True:
        h = fetch({'year': day1[:4], 'hg': '', 'dept': '', 'mok': '', 'saup': '',
                   'day1': day1, 'day2': day2, 'p': p})
        rows = parse_rows(h)
        if not rows:
            break
        first = rows[0].get('번호')
        if first == prev_first:                 # 마지막 페이지 초과 → 같은 페이지 반복
            break
        prev_first = first
        for r in rows:
            add(archive, r); n += 1
        if tag and p % 100 == 0:
            print(f'    {tag} p{p} ({n}건)', flush=True)
        if len(rows) < 10:
            break
        p += 1
        time.sleep(sleep)
    return n


def save(archive, extra=None):
    biz = {k: {mok: _out_node(nd) for mok, nd in moks.items()} for k, moks in archive.items()}
    out = {'biz': biz, 'source': '무주군 재정정보공개(copen.muju.go.kr) expenditurelist'}
    if extra:
        out.update(extra)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    return os.path.getsize(OUT)


def _mend(y, m):
    return (date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)).day


def _drange(a, b):
    """YYYYMMDD a~b 포함 날짜 문자열 목록."""
    da = date(int(a[:4]), int(a[4:6]), int(a[6:8]))
    db = date(int(b[:4]), int(b[4:6]), int(b[6:8]))
    out = []
    while da <= db:
        out.append(da.strftime('%Y%m%d')); da += timedelta(days=1)
    return out


def backfill(year=None):
    y = year or str(date.today().year)
    today = date.today()
    months = []
    m = 1
    while m <= today.month:
        d1 = f'{y}{m:02d}01'
        d2 = today.strftime('%Y%m%d') if m == today.month else f'{y}{m:02d}{_mend(int(y), m):02d}'
        months.append((d1, d2)); m += 1
    # 재개: 기존 파일에 months_done 있으면 그 달들은 건너뜀(중복합산 방지). 없으면 처음부터.
    archive, days, done = {}, [], []
    if os.path.exists(OUT):
        prev = json.load(open(OUT, encoding='utf-8'))
        if prev.get('months_done'):
            archive = rehydrate(prev.get('biz', {}))
            days = list(prev.get('days', []))
            done = list(prev.get('months_done', []))
            print(f'  재개: 완료월 {done} 건너뜀 (세부사업 {len(archive)}종 로드)', flush=True)
    grand, t0 = 0, time.time()
    for d1, d2 in months:
        mm = d1[:6]
        if mm in done:
            continue
        t = time.time()
        n = scan_range(d1, d2, archive, tag=mm)
        grand += n
        days += _drange(d1, d2)                                     # 이 달의 모든 날짜 = 처리 완료 표시
        done.append(mm)
        sz = save(archive, {'asof': today.strftime('%Y%m%d'), 'days': days, 'months_done': done})   # 중간저장(재개 안전)
        print(f'  {mm}: {n}건 / {time.time()-t:.0f}s / 세부사업 {len(archive)}종 / {sz//1024}KB (중간저장)', flush=True)
    print(f'✓ 총 {grand}건 신규 → muju_exec_biz.json / {time.time()-t0:.0f}s', flush=True)


def incremental(day=None):
    """기존 아카이브에 미처리 날짜만 병합(중복방지). day 지정 시 그 하루만.
    day 없으면 마지막 처리일+1 ~ 어제 중 안 한 날 전부(놓친 날 자동 보충)."""
    if os.path.exists(OUT):
        prev = json.load(open(OUT, encoding='utf-8'))
        archive = rehydrate(prev.get('biz', {}))
        done = set(prev.get('days', []))
        asof0 = prev.get('asof', '')
    else:
        archive, done, asof0 = {}, set(), ''
    yest = (date.today() - timedelta(days=1)).strftime('%Y%m%d')
    if day:
        targets = [day] if day not in done else []
    else:
        start = (max(done) if done else f'{date.today().year}0101')
        span = _drange(start, yest)[1:] if done else _drange(f'{date.today().year}0101', yest)
        targets = [d for d in span if d not in done]
    if not targets:
        print(f'✓ 증분: 이미 최신({asof0}) — 처리할 날짜 없음', flush=True); return
    t, tot = time.time(), 0
    for d in targets:
        tot += scan_range(d, d, archive)
        done.add(d)
    asof = max([asof0] + sorted(done)) if done else yest
    sz = save(archive, {'asof': asof, 'days': sorted(done)})
    print(f'✓ 증분 {targets[0]}~{targets[-1]} ({len(targets)}일): {tot}건 병합 → 세부사업 {len(archive)}종 / {sz//1024}KB / {time.time()-t:.0f}s', flush=True)


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'backfill'
    if mode == 'backfill':
        backfill()
    elif mode == 'inc':
        incremental(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        print('usage: muju_exec.py [backfill | inc [YYYYMMDD]]'); sys.exit(1)
