"""config(regions·datasets·build) 읽어 → lofin 집계 → data/summary.json + 옵시디언 마크다운.
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


def cfg(name):
    return json.load(open(os.path.join(ROOT, 'config', name), encoding='utf-8'))


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


def build():
    regions = cfg('regions.json')['regions']
    datasets = {d['id']: d for d in cfg('datasets.json')['datasets']}
    bc = cfg('build.json')
    region = next(r for r in regions if r['id'] == bc['region'])
    ds = datasets[bc['datasets'][0]]                       # 1차 = 세출집행(QWGJK)
    fyr = bc['years'][0]
    F, A, flt = ds['fields'], ds['amounts'], ds['filters']
    asof = find_asof(ds, fyr) if bc.get('asof') == 'latest' else bc['asof']
    print(f'기준일(asof) = {asof}')

    units_out, home = [], None
    for u in region['units']:
        rws = lofin.rows(ds['endpoint'], {flt['year']: fyr, flt['asof']: asof, flt['unit']: u['laf_cd']})
        budget = sum(_int(x.get(A['budget'])) for x in rws)
        spent = sum(_int(x.get(A['spent'])) for x in rws)
        byf = {}
        for x in rws:
            e = byf.setdefault(x.get(F['field']) or '기타', {'budget': 0, 'spent': 0})
            e['budget'] += _int(x.get(A['budget']))
            e['spent'] += _int(x.get(A['spent']))
        units_out.append({
            'name': u['name'], 'laf_cd': u['laf_cd'], 'type': u['type'], 'home': u.get('home', False),
            'budget': budget, 'spent': spent, 'rate': round(spent / budget * 100, 1) if budget else 0,
            'biz_count': len(rws), 'fields': byf,
        })
        print(f"  {u['name']}: 편성 {budget // 100000000}억 / 지출 {spent // 100000000}억 / {len(rws)}사업")
        if u.get('home'):
            byd = {}
            for x in rws:
                e = byd.setdefault(x.get(F['dept']) or '?', {'budget': 0, 'spent': 0, 'count': 0})
                e['budget'] += _int(x.get(A['budget']))
                e['spent'] += _int(x.get(A['spent']))
                e['count'] += 1
            top = sorted(rws, key=lambda x: -_int(x.get(A['spent'])))[:30]
            home = {
                'name': u['name'], 'depts': byd,
                'top_biz': [{'biz': x.get(F['biz']), 'field': x.get(F['field']), 'part': x.get(F['part']),
                             'budget': _int(x.get(A['budget'])), 'spent': _int(x.get(A['spent']))} for x in top],
            }

    summary = {
        'region': region['name'], 'dataset': ds['name'], 'fyr': fyr, 'asof': asof,
        'updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'units': units_out, 'home': home,
    }
    os.makedirs(os.path.join(ROOT, 'data'), exist_ok=True)
    json.dump(summary, open(os.path.join(ROOT, 'data', 'summary.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f"✓ data/summary.json — {region['name']} {len(units_out)}개 시군")

    if 'obsidian' in bc.get('exports', []):
        export_obsidian(summary)
    return summary


def export_obsidian(s):
    L = [f"# {s['region']} 세출예산 집행 ({s['fyr']})", '',
         f"> 갱신 {s['updated']} · 기준일 {s['asof']} · 출처 지방재정365", '',
         '## 14개 시군 집행률', '',
         '| 시군 | 편성(억) | 지출(억) | 집행률 |', '|---|---|---|---|']
    for u in sorted(s['units'], key=lambda u: -u['rate']):
        star = '⭐ ' if u['home'] else ''
        L.append(f"| {star}{u['name']} | {u['budget'] // 100000000:,} | {u['spent'] // 100000000:,} | {u['rate']}% |")
    p = os.path.join(ROOT, 'exports', 'obsidian', f"{s['region']}_세출집행.md")
    open(p, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print(f'✓ {p}')


if __name__ == '__main__':
    build()
