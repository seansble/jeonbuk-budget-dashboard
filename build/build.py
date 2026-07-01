"""config(regions·datasets·build) 읽어 → lofin 집계 → data/summary.json + 원본 + 옵시디언 마크다운.
config-driven 이라 코드 안 건드리고 확장(지자체·데이터셋·연도·출력)."""
import os
import sys
import re
import json
import datetime
import lofin

# 사업명 정규화 — 시군마다 같은 국고보조사업을 '지원사업/지급/지급(보조)' 등 다르게 부름.
# 괄호·공백 제거 + 꼬리 처리어 제거 → 동일사업 매칭(고유가 피해지원금 = …지원사업 = …지급사업).
# 꼬리 처리어 — 긴 것 먼저. 같은 사업을 시군마다 '지원사업/지급/실시/지원' 등 다르게 표기.
_STRIP = ['지원사업', '지급사업', '지급보조', '운영지원', '보조사업', '추진사업',
          '지원금', '지급', '지원', '실시', '추진', '운영', '보조', '사업', '관리', '구입', '구매']


def _norm(nm):
    if not nm:
        return ''
    s = re.sub(r'\(.*?\)', '', nm)
    s = re.sub(r'\s+', '', s)
    for _ in range(3):                         # 꼬리말 최대 3겹 제거(…지원금지원사업 등)
        for suf in _STRIP:
            if s.endswith(suf) and len(s) > len(suf) + 1:
                s = s[:-len(suf)]
                break
    return s


def _bg(s):                                    # 글자 2-gram 집합(유사도용)
    return set(s[i:i + 2] for i in range(len(s) - 1)) or ({s} if s else set())


def _jac(a, b):                                # 자카드 유사도
    return len(a & b) / len(a | b) if (a or b) else 0.0


_SIM_TH = 0.45                                  # 이 이상 닮으면 '무주가 사실상 보유'로 간주(오탐 제거)
_MIN_GRANT = 10_000_000                          # 국도비 비교 최소 평균액(1천만) — 소액사업은 의미없어 제외


def _has_similar(key, home_bg, home_norm):
    """무주가 유사사업 보유? 자카드 0.45+ 또는 핵심어 포함관계(4자+)."""
    kb = _bg(key)
    if any(_jac(kb, hb) >= _SIM_TH for hb in home_bg):
        return True
    if len(key) >= 4:                           # 한쪽이 다른 쪽에 통째로 들어감(관찰포운영·정부양곡관리 등)
        for hk in home_norm:
            if len(hk) >= 4 and (key in hk or hk in key):
                return True
    return False

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


_KST = datetime.timezone(datetime.timedelta(hours=9))    # Actions 러너=UTC라 KST 고정(안 하면 갱신시각 9h 밀림)


def _now_kst():
    return datetime.datetime.now(_KST)


def recent_bizdays(n=20):
    d = _now_kst().date()
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime('%Y%m%d'))
        d -= datetime.timedelta(days=1)
    return out


def find_asof(ds, fyr, probe_cd):
    """asof=latest — 데이터 있는 최근 영업일 자동 탐색 + 완전성 가드.
    정부(지방재정365)는 새 날짜 스냅샷을 아침에 부분만 올리고 하루 종일 채운다.
    '데이터 있음'만 보면 미완성 스냅샷(전 시군 row 급감)을 잡으므로,
    대표 시군(probe_cd)의 최근 영업일 row 수를 비교해 급감한 날짜는 건너뛰고
    완전한(peak의 90%+) 최신 날짜를 고른다. 누적 집행이라 정상일은 단조 증가."""
    f = ds['filters']
    days = recent_bizdays(20)
    counts = {}
    for ymd in days:                              # 최신→과거, 완전한 표본 5개 모이면 중단
        n = len(lofin.rows(ds['endpoint'], {f['year']: fyr, f['asof']: ymd, f['unit']: probe_cd}))
        if n:
            counts[ymd] = n
        if len(counts) >= 5:
            break
    if not counts:
        return None
    peak = max(counts.values())
    for ymd in days:                              # 최신순으로 첫 완전 날짜(미완성 급감분 스킵)
        if counts.get(ymd, 0) >= peak * 0.9:
            return ymd
    return None


def _sum(rows, key):
    return sum(_int(x.get(key)) for x in rows)


def build():
    regions = cfg('regions.json')['regions']
    datasets = {d['id']: d for d in cfg('datasets.json')['datasets']}
    dept_map = cfg('dept_map.json', optional=True)
    dept_order = cfg('dept_order.json', optional=True)
    population = cfg('population.json', optional=True)
    pop_map = population.get('pop', {})                    # laf_cd → 주민등록 인구(1인당 지표용)
    bc = cfg('build.json')
    region = next(r for r in regions if r['id'] == bc['region'])
    ds = datasets[bc['datasets'][0]]                       # 1차 = 세출집행(QWGJK)
    fyr = bc['years'][0]
    F, A, flt = ds['fields'], ds['amounts'], ds['filters']
    exports = bc.get('exports', [])
    probe_cd = region['units'][0]['laf_cd']               # 대표 시군(최대규모) = 완전성 판단 기준
    asof = find_asof(ds, fyr, probe_cd) if bc.get('asof') == 'latest' else bc['asof']
    print(f'기준일(asof) = {asof}')

    if 'raw' in exports:
        os.makedirs(os.path.join(ROOT, 'data', 'raw'), exist_ok=True)

    units_out, home, home_name, home_biz = [], None, None, set()
    grant = {}                                            # 사업명 → {field, units:{시군:국도비액}} (시군비교용)
    for u in region['units']:
        rws = lofin.rows(ds['endpoint'], {flt['year']: fyr, flt['asof']: asof, flt['unit']: u['laf_cd']})

        for x in rws:                                     # 국·도비 받는 사업 인덱스(정규화 키로 묶음)
            nm = x.get(F['biz'])
            g = _int(x.get(A['natl'])) + _int(x.get(A['prov']))
            if nm and g > 0:
                key = _norm(nm)
                e = grant.setdefault(key, {'field': x.get(F['field']), 'names': {}, 'units': {}})
                e['names'][nm] = e['names'].get(nm, 0) + 1
                e['units'][u['name']] = e['units'].get(u['name'], 0) + g

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
        pop = _int(pop_map.get(u['laf_cd']))              # 1인당 재정: 절대액은 규모편향, 1인당이 공정비교
        units_out.append({
            'name': u['name'], 'laf_cd': u['laf_cd'], 'type': u['type'], 'home': u.get('home', False),
            'budget': budget, 'spent': spent, 'rate': round(spent / budget * 100, 1) if budget else 0,
            'biz_count': len(rws), 'fields': byf,
            'natl': natl, 'prov': prov, 'local': local,
            'natl_rate': round(natl / budget * 100, 1) if budget else 0,
            'pop': pop,
            'pc_budget': round(budget / pop) if pop else 0,   # 1인당 편성
            'pc_spent': round(spent / pop) if pop else 0,     # 1인당 지출
            'pc_natl': round(natl / pop) if pop else 0,       # 1인당 국비확보
            'pc_local': round(local / pop) if pop else 0,     # 1인당 자체재원(군비)
        })
        print(f"  {u['name']}: 편성 {budget // 100000000}억 / 지출 {spent // 100000000}억 / "
              f"국비 {natl // 100000000}억 / {len(rws)}사업")

        if u.get('home'):
            home = build_home(u, rws, F, A, dept_map.get(u['laf_cd'], {}), dept_order.get(u['laf_cd'], []))
            home_name = u['name']
            home_biz = set(_norm(x.get(F['biz'])) for x in rws)   # 정규화로 비교
            home_norm = [k for k in home_biz if k]
            home_bg = [_bg(k) for k in home_norm]                 # 유사도 비교용 2-gram

    if home:                                              # 무주군에 없는 국·도비 사업(다른 시군은 받음)
        miss = []
        for key, e in grant.items():
            if key in home_biz:                           # 무주가 (이름변형 포함) 이미 하는 사업 제외
                continue
            us = {k: v for k, v in e['units'].items() if k != home_name}
            if len(us) < 2:                               # 최소 2개 시군이 받아야 = 보편 사업
                continue
            if _has_similar(key, home_bg, home_norm):     # 유사사업 보유 시 제외(배치↔활동지원·포함관계 오탐)
                continue
            avg = sum(us.values()) // len(us)
            if avg < _MIN_GRANT:                          # 소액(평균 1천만 미만)은 의미없어 제외
                continue
            top = max(us.items(), key=lambda kv: kv[1])
            disp = max(e['names'].items(), key=lambda kv: kv[1])[0]   # 대표 표기 = 최빈 원본명
            miss.append({'biz': disp, 'field': e['field'], 'n_units': len(us),
                         'avg': avg, 'max': top[1], 'max_unit': top[0]})
        miss.sort(key=lambda m: (-m['n_units'], -m['avg']))
        home['missing_grants'] = miss[:25]
        print(f"  무주군에 없는 국·도비 사업: {len(miss)}건 (상위 25 저장)")

    summary = {
        'region': region['name'], 'dataset': ds['name'], 'fyr': fyr, 'asof': asof,
        'updated': _now_kst().strftime('%Y-%m-%d %H:%M'),
        'pop_asof': population.get('asof'), 'pop_source': population.get('source'),
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


def build_home(u, rws, F, A, names, order=()):
    """홈 지자체 = 부서별(부서명)→세부사업명 드릴다운 + 재원.
    같은 부서명으로 매핑된 코드(예: 6개 읍·면사무소)는 하나로 합산. order=직제순 부서명 리스트."""
    byd = {}
    for x in rws:
        code = x.get(F['dept']) or '?'
        name = names.get(code, code)               # 표시명 = 병합 키
        e = byd.setdefault(name, {'name': name, 'codes': set(),
                                  'budget': 0, 'spent': 0, 'natl': 0, 'prov': 0, 'local': 0, 'biz': []})
        e['codes'].add(code)
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
        e['codes'] = sorted(e['codes'])            # set → JSON 직렬화 가능
        depts.append(e)
    rank = {nm: i for i, nm in enumerate(order)}   # 직제순(없으면 뒤로, 편성액순 보조)
    depts.sort(key=lambda d: (rank.get(d['name'], 9999), -d['budget']))
    top = sorted(rws, key=lambda x: -_int(x.get(A['spent'])))[:30]

    own = []                                       # 순수 군비사업: 국비0·도비0, 시군비만 → 군 자체 우선순위
    for x in rws:
        n, p, l = _int(x.get(A['natl'])), _int(x.get(A['prov'])), _int(x.get(A['local']))
        if n == 0 and p == 0 and l > 0:
            own.append({'biz': x.get(F['biz']), 'field': x.get(F['field']),
                        'dept': names.get(x.get(F['dept']) or '', x.get(F['dept']) or '?'),
                        'local': l, 'budget': _int(x.get(A['budget'])), 'spent': _int(x.get(A['spent']))})
    own.sort(key=lambda b: -b['local'])
    for b in own:
        b['rate'] = round(b['spent'] / b['budget'] * 100) if b['budget'] else 0

    return {
        'name': u['name'], 'laf_cd': u['laf_cd'], 'depts': depts,
        'local_only': own[:30],
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
