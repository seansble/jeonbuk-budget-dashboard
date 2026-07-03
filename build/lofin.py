"""범용 lofin OpenAPI 호출기 — datasets.json 정의만 보고 어느 hub·laf_cd·연도든 호출.
키는 환경변수 LOFIN_KEY(Actions=Secret) 우선, 없으면 로컬 .env 폴백."""
import os
import json
import ssl
import time
import urllib.request
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))


def _key():
    k = os.environ.get('LOFIN_KEY')
    if k:
        return k
    for p in (os.path.join(_HERE, '..', '.env'), os.path.join(_HERE, '.env')):
        try:
            with open(p, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('LOFIN_KEY='):
                        return line.split('=', 1)[1].strip().strip('"').strip("'")
        except FileNotFoundError:
            pass
    return ''


# 정부 사이트가 구형 TLS 암호를 써서 Python 기본 SSL 이 핸드셰이크 거부 → 보안레벨 1 허용
_CTX = ssl.create_default_context()
_CTX.set_ciphers('DEFAULT@SECLEVEL=1')


def fetch(endpoint, params, timeout=40, retries=3):
    """단일 호출 — JSON dict 반환. 일시 타임아웃/네트워크 오류는 재시도(gov 아침 갱신 시간대 느림)."""
    p = dict(params)
    p.setdefault('Type', 'json')
    k = _key()
    if k:
        p['Key'] = k
    url = endpoint + '?' + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:                     # URLError(timeout)·ConnectionReset 등 일시 장애
            last = e
            if i < retries - 1:
                time.sleep(5 * (i + 1))            # 5s → 10s 백오프
    raise last


def rows(endpoint, params, max_pages=20, size=1000, key=None):
    """페이지네이션해서 전체 row 수집. 응답 루트키(hub 이름)는 자동 탐색.
    key=중복판별 필드 리스트(datasets.json row_key) — gov가 스냅샷을 채우는 중에 fetch하면
    페이지 사이 row가 밀려 같은 사업이 두 페이지에 걸쳐 잡힘(금액만 미묘하게 다름) →
    합산 부풀림. 같은 키는 뒤 페이지(최신) 값만 유지."""
    out = []
    for pi in range(1, max_pages + 1):
        p = dict(params)
        p['pIndex'] = pi
        p['pSize'] = size
        d = fetch(endpoint, p)
        root = next((v for v in d.values() if isinstance(v, list)), [])
        rr = []
        for blk in root:
            if isinstance(blk, dict) and 'row' in blk:
                rr = blk['row']
        out += rr
        if len(rr) < size:
            break
    if key:
        best = {}
        for r in out:
            best[tuple(r.get(k) for k in key)] = r   # 뒤 페이지 = 최신값 우선
        out = list(best.values())
    return out
