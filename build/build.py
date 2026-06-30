"""config(regions·datasets·build) 읽어 → lofin 집계 → data/summary.json + 원본 + 옵시디언 마크다운.
config-driven 이라 코드 안 건드리고 확장(지자체·데이터셋·연도·출력)."""
import os
import sys
import json
import datetime
import lofin

try:                                      # Windows 콘솔 cp949 → UTF-8 (Linux/Actions 무해)
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def cfg(name, optional=False):
    p = os.path.join(ROOT, 'config', name)
    if optional and not os.path.exists(p):
        return {}
    return json.load(open(p, encoding='utf-8'))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def recent_bizdays(n=20):
    d = datetime.date.today()
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime('%Y%m%d'))
        d -= datetime.timedelta(days=1)
    return out


def find_asof(ds, fyr):
    """asof=latest — 데이터 있는 최근 영업일 자동 탐색."""
    yf, af = ds['filters']['year'], ds['filters']['asof']
    for ymd in recent_bizdays(20):
        if lofin.rows(ds['endpoint'], {yf: fyr, af: ymd}, max_pages=1, size=1):
            return ymd
    return None


def _sum(rows, key):
    return sum(_int(x.get(key)) for x in rows)


def build():
    regions = cfg('regions.json')['regions']
    datasets = {d['id']: d for d in cfg('datasets.json')['datasets']}
    dept_map = cfg('dept_map.json', optional=True)
    bc = cfg('build.json')
    region = next(r for r in regions if r['id'] == bc['region'])
    ds = datasets[bc['datasets'][0]]                       # 1차 = 세출집행(QWGJK)
    fyr = bc['years'][0]
    F, A, flt = ds['fields'], ds['amounts'], ds['filters']
    exports = bc.get('exports', [])
    asof = find_asof(ds, fyr) if bc.get('asof') == 'latest' else bc['asof']
    print(f'기준일(asof) = {asof}')

    if 'raw' in exports:
        os.makedirs(os.path.join(ROOT, 'data', 'raw'), exist_ok=True)

    units_out, home = [], None
    for u in region['units']:
        rws = lofin.rows(ds['endpoint'], {flt['year']: fyr, flt['asof']: asof, flt['unit']: u['laf_cd']})

        if 'raw' in exports:                              # 원본 = API 제공 형태 그대로 저장
            json.dump({'laf_cd': u['laf_cd'], 'name': u['name'], 'fyr': fyr, 'asof': asof,
                       'dataset': ds['id'], 'rows': rws},
                      open(os.path.join(ROOT, 'data', 'raw', f"{u['laf_cd']}_{fyr}.json"), 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)

        budget, spent = _sum(rws, A['budget']), _sum(rws, A['spent'])
        natl, prov, local = _sum(rws, A['natl']), _sum(rws, A['prov']), _sum(rws, A['local'])
        byf = {}
        for x in rws:
            e = byf.setdefault(x.get(F['field']) or '기타', {'budget': 0, 'spent': 0})
            e['budget'] += _int(x.get(A['budget']))
            e['spent'] += _int(x.get(A['spent']))
        units_out.append({
            'name': u['name'], 'laf_cd': u['laf_cd'], 'type': u['type'], 'home': u.get('home', False),
            'budget': budget, 'spent': spent, 'rate': round(spent / budget * 100, 1) if budget else 0,
            'biz_count': len(rws), 'fields': byf,
            'natl': natl, 'prov': prov, 'local': local,
            'natl_rate': round(natl / budget * 100, 1) if budget else 0,
        })
        print(f"  {u['name']}: 편성 {budget // 100000000}억 / 지출 {spent // 100000000}억 / "
              f"국비 {natl // 100000000}억 / {len(rws)}사업")

        if u.get('home'):
            home = build_home(u, rws, F, A, dept_map.get(u['laf_cd'], {}))

    summary = {
        'region': region['name'], 'dataset': ds['name'], 'fyr': fyr, 'asof': asof,
        'updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'units': units_out, 'home': home,
    }
    os.makedirs(os.path.join(ROOT, 'data'), exist_ok=True)
    json.dump(summary, open(os.path.join(ROOT, 'data', 'summary.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f"✓ data/summary.json — {region['name']} {len(units_out)}개 시군"
          + (f" + 원본 data/raw/" if 'raw' in exports else ''))

    if 'obsidian' in exports:
        export_obsidian(summary)
    return summary


def build_home(u, rws, F, A, names):
    """홈 지자체 = 부서별(부서명)→세부사업명 드릴다운 + 재원."""
    byd = {}
    for x in rws:
        code = x.get(F['dept']) or '?'
        e = byd.setdefault(code, {'code': code, 'name': names.get(code, code),
                                  'budget': 0, 'spent': 0, 'natl': 0, 'prov': 0, 'local': 0, 'biz': []})
        e['budget'] += _int(x.get(A['budget']))
        e['spent'] += _int(x.get(A['spent']))
        e['natl'] += _int(x.get(A['natl']))
        e['prov'] += _int(x.get(A['prov']))
        e['local'] += _int(x.get(A['local']))
        e['biz'].append({
            'biz': x.get(F['biz']), 'field': x.get(F['field']), 'part': x.get(F['part']),
            'budget': _int(x.get(A['budget'])), 'spent': _int(x.get(A['spent'])),
            'natl': _int(x.get(A['natl'])), 'prov': _int(x.get(A['prov'])), 'local': _int(x.get(A['local'])),
        })
    depts = []
    for e in byd.values():
        e['biz'].sort(key=lambda b: -b['spent'])
        e['rate'] = round(e['spent'] / e['budget'] * 100, 1) if e['budget'] else 0
        e['count'] = len(e['biz'])
        depts.append(e)
    depts.sort(key=lambda d: -d['budget'])
    top = sorted(rws, key=lambda x: -_int(x.get(A['spent'])))[:30]
    return {
        'name': u['name'], 'laf_cd': u['laf_cd'], 'depts': depts,
        'top_biz': [{'biz': x.get(F['biz']), 'field': x.get(F['field']), 'part': x.get(F['part']),
                     'budget': _int(x.get(A['budget'])), 'spent': _int(x.get(A['spent']))} for x in top],
    }


def export_obsidian(s):
    L = [f"# {s['region']} 세출예산 집행 ({s['fyr']})", '',
         f"> 갱신 {s['updated']} · 기준일 {s['asof']} · 출처 지방재정365", '',
         '## 14개 시군 집행률 · 국비', '',
         '| 시군 | 편성(억) | 지출(억) | 집행률 | 국비(억) | 국비비중 |',
         '|---|---|---|---|---|---|']
    for u in sorted(s['units'], key=lambda u: -u['rate']):
        star = '⭐ ' if u['home'] else ''
        L.append(f"| {star}{u['name']} | {u['budget'] // 100000000:,} | {u['spent'] // 100000000:,} | "
                 f"{u['rate']}% | {u['natl'] // 100000000:,} | {u['natl_rate']}% |")
    h = s.get('home')
    if h:
        L += ['', f"## {h['name']} 부서별 (편성순)", '', '| 부서 | 편성(억) | 집행률 | 사업수 |', '|---|---|---|---|']
        for d in h['depts']:
            L.append(f"| {d['name']} | {d['budget'] // 100000000:,} | {d['rate']}% | {d['count']} |")
    p = os.path.join(ROOT, 'exports', 'obsidian', f"{s['region']}_세출집행.md")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print(f'✓ {p}')


if __name__ == '__main__':
    build()
