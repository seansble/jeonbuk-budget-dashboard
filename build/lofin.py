"""범용 lofin OpenAPI 호출기 — datasets.json 정의만 보고 어느 hub·laf_cd·연도든 호출.
키는 환경변수 LOFIN_KEY(Actions=Secret) 우선, 없으면 로컬 .env 폴백."""
import os
import json
import ssl
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


def fetch(endpoint, params, timeout=40):
    """단일 호출 — JSON dict 반환."""
    p = dict(params)
    p.setdefault('Type', 'json')
    k = _key()
    if k:
        p['Key'] = k
    url = endpoint + '?' + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.loads(r.read().decode('utf-8'))


def rows(endpoint, params, max_pages=20, size=1000):
    """페이지네이션해서 전체 row 수집. 응답 루트키(hub 이름)는 자동 탐색."""
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
    return out
