"""config(regions·datasets·build) 읽어 → lofin 집계 → data/summary.json + 원본 + 옵시디언 마크다운.
config-driven 이라 코드 안 건드리고 확장(지자체·데이터셋·연도·출력)."""
import os
import sys
import re
import json
import socket
import calendar
import datetime
import urllib.error
import lofin

# lofin(정부 .go.kr) 접속 자체가 안 될 때의 예외 — GitHub 러너(해외 클라우드 IP)는
# gov 방화벽에 SYN 이 침묵 드롭돼 connect timeout 남(한국 IP 는 정상). 이건 우리 버그가
# 아니라 네트워크 경로 차단 → 빌드를 죽이지 말고 기존 data/ 유지한 채 green 으로 종료.
_NET_ERRS = (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout, socket.gaierror)

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
        n = len(lofin.rows(ds['endpoint'], {f['year']: fyr, f['asof']: ymd, f['unit']: probe_cd}, key=ds.get('row_key')))
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


def fetch_indicators(region_wa, ind_cfg):
    """재정지표(주민1인당, 연간) OpenAPI → {laf_cd: {key:원, 'pop':명}}.
    ★ 안전패턴: 전부 성공하면 캐시 저장+사용, 하나라도 실패하면 마지막 캐시 폴백
    → 빌드 절대 안 깨짐(연간 데이터라 폴백값도 최신에 가까움)."""
    fyr = ind_cfg.get('fyr')
    items = ind_cfg.get('items', [])
    cache_path = os.path.join(ROOT, 'data', 'indicators_cache.json')
    fresh, all_ok = {}, True
    for it in items:
        ep = 'https://www.lofin365.go.kr/lf/hub/' + it['id']
        try:
            rws = lofin.rows(ep, {'fyr': fyr, 'wa_laf_cd': region_wa})
            if not rws:
                raise ValueError('빈 응답')
            for r in rws:
                cd = r.get('laf_cd')
                d = fresh.setdefault(cd, {})
                d[it['key']] = _int(r.get(it['field'])) * 1000   # 천원 → 원
                d['pop'] = _int(r.get('pptn_num'))
            print(f"  지표 {it['name']}({it['id']}): {len(rws)}건")
        except Exception as e:
            all_ok = False
            print(f"  ! 지표 {it['id']} 실패 → 캐시 폴백: {e}")
    if all_ok and fresh:
        json.dump(fresh, open(cache_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        return fresh
    try:
        return json.load(open(cache_path, encoding='utf-8'))   # 폴백
    except FileNotFoundError:
        return fresh


RACE_YEARS = 10                                          # 경주 표시 연수(10개년)


def build_race(units_cfg, ds, units_cur, fyr):
    """10개년 14시군 전체 세출예산(cpl_amt, 국·도비 포함) 총액 → bar chart race 데이터.
    ★ 과거 연도는 확정값이라 race_cache.json 캐시(연말 asof, 1회만 API) → 매일빌드 부하 없음.
    올해(fyr)는 units_cur(이번 빌드 집계)의 budget 사용."""
    A, flt = ds['amounts'], ds['filters']
    fy = int(fyr)
    y0 = fy - RACE_YEARS + 1
    past = [(str(y), f'{y}1231') for y in range(y0, fy)]
    cache_path = os.path.join(ROOT, 'data', 'race_cache.json')
    try:
        cache = json.load(open(cache_path, encoding='utf-8'))
    except FileNotFoundError:
        cache = {}
    changed = False
    for yr, ymd in past:
        if yr in cache:
            continue
        cache[yr] = {}
        for u in units_cfg:
            rws = lofin.rows(ds['endpoint'], {flt['year']: yr, flt['asof']: ymd, flt['unit']: u['laf_cd']}, key=ds.get('row_key'))
            cache[yr][u['laf_cd']] = _sum(rws, A['budget'])   # 전체예산(국·도비 포함)
        changed = True
        print(f"  race {yr}: {len(cache[yr])}개 시군 캐시")
    if changed:
        json.dump(cache, open(cache_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    cur = {u['laf_cd']: u for u in units_cur}
    years = [str(y) for y in range(y0, fy + 1)]
    out = []
    for u in units_cfg:
        vals = [cache.get(str(y), {}).get(u['laf_cd'], 0) for y in range(y0, fy)]
        vals.append(cur.get(u['laf_cd'], {}).get('budget', 0))
        out.append({'name': u['name'], 'type': u['type'], 'home': u.get('home', False), 'values': vals})
    return {'years': years, 'units': out}


# 신속집행 제외(경직성) 통계목 키워드 — 나머지는 신속집행 대상(소비·투자). 지자체 표준 규칙.
# 무주 신속집행 대상 42개 통계목(공식: 기획조정실-1741 추진계획 · 소비 37 + 투자 5).
# 직접집행경비(인건비·물건비·여비·업무추진비·의회비·재료·연구·시설비·자산취득) — 이전지출(보조/위탁/수혜금/출연)은 제외.
SOKSOK_CODES = set()
for _g, _ss in {'101': (1, 2, 3, 4), '201': (1, 2, 3, 4, 5), '202': (1, 2, 3, 4, 5),
                '203': (1, 2, 3, 4), '204': (1, 3), '205': tuple(range(1, 13)), '206': (1,),
                '207': (1, 2, 3), '401': (1, 2, 3, 4), '405': (1, 2)}.items():
    for _n in _ss:
        SOKSOK_CODES.add(f'{_g}-{_n:02d}')
SOKSOK_NAMES = set()          # 원장(통계목 이름만) 매칭용 — muju_stat 코드에서 이름 추출


def _sok_code(g, s):
    return f"{g.get('code')}-{s.get('code')}" in SOKSOK_CODES


def _build_soksok_names(stat):
    SOKSOK_NAMES.clear()
    for v in stat.values():
        for g in v.get('groups', []):
            for s in g.get('stats', []):
                if _sok_code(g, s):
                    SOKSOK_NAMES.add(s['name'])


def is_soksok(mok):
    """통계목명이 신속집행 대상 42개인가 (SOKSOK_NAMES = stat 코드에서 구축)."""
    return mok in SOKSOK_NAMES


def _tree_norm(s):
    """세부사업명/통계목명 정규화(공백·괄호·중점 제거) — 소스별 표기차 흡수."""
    return re.sub(r'[\s()（）·ㆍ]', '', s or '')


# 신속집행 대상 통계목(공식 기초36 중 무주 사용분) — 이름 매칭용
SOK_NAMES = {'보수', '기타직보수', '맞춤형복지제도시행경비', '재료비', '연구용역비', '전산개발비', '출연금',
             '민간경상사업보조', '민간위탁금', '자치단체간부담금', '교육기관에대한보조', '예비군육성지원경상보조',
             '공기관등에대한경상적위탁사업비', '공무원연금관리공단경상전출금', '시설비', '감리비', '시설부대비',
             '민간자본사업보조(자체재원)', '민간자본사업보조(이전재원)', '민간위탁사업비',
             '공기관등에대한자본적위탁사업비', '예비군육성지원자본보조'}


def build_race2(exec_path, stat_path, official_path):
    """소비투자·신속집행 2축 월별 러닝(편성부서 기준). 원장 월별 집행을 공식 스냅샷(기준일)에 보정.
    실적률 = 누계집행 ÷ 목표(공식). 소비투자=42통계목·분기목표, 신속집행=36통계목·상반기60%목표."""
    try:
        ebj = json.load(open(exec_path, encoding='utf-8'))
        ex = ebj.get('biz', {})
        stat = json.load(open(stat_path, encoding='utf-8'))
        off = json.load(open(official_path, encoding='utf-8'))
    except FileNotFoundError:
        return None
    if not ex:
        return None
    # ★ 미완성 당월 드롭: 원장 asof가 그 달 말일 전이면 진행중(부분집계)이라 러닝 끝이 평평해짐 → 완료월까지만.
    drop_mm = None
    ea = ebj.get('asof', '')
    if len(ea) == 8:
        try:
            if int(ea[6:8]) < calendar.monthrange(int(ea[:4]), int(ea[4:6]))[1]:
                drop_mm = ea[4:6]
        except ValueError:
            pass
    _build_soksok_names(stat)                       # 소비투자 42통계목 이름(SOKSOK_NAMES)
    sobi_names, sok_names = set(SOKSOK_NAMES), SOK_NAMES
    biz2dept = {}
    for k in stat:
        dp, bz = (k.split('\x01') + [''])[:2]
        biz2dept.setdefault(_tree_norm(bz), dp)

    def eupbu(k):
        return k.split('\x01')[0] if k in stat else biz2dept.get(_tree_norm(k.split('\x01')[1] if '\x01' in k else k))

    def per_month(names):
        per, allm = {}, set()
        for k, moks in ex.items():
            dept = eupbu(k)
            if not dept:
                continue
            for mok, nd in moks.items():
                if mok not in names:
                    continue
                for mm, amt in (nd.get('m') or {}).items():
                    if mm == drop_mm:                # 진행중 당월 제외
                        continue
                    per.setdefault(dept, {})[mm] = per.setdefault(dept, {}).get(mm, 0) + amt
                    allm.add(mm)
        return per, sorted(allm)

    asof = off.get('asof', '2026-03-17')
    asof_mm = asof[5:7]                             # '03'

    def axis(names, off_depts):
        per, mms = per_month(names)
        offmap = {d['name']: d for d in off_depts}
        li = mms.index(asof_mm) if asof_mm in mms else len(mms) - 1
        depts = []
        for dept, mv in per.items():
            od = offmap.get(dept)
            if not od or not od.get('target'):
                continue
            cum, vals = 0, []
            for m in mms:
                cum += mv.get(m, 0); vals.append(cum)
            led_at = vals[li] or 0
            scale = (od['exec'] / led_at) if led_at else 0        # 원장→공식 집행 보정(기준일 일치)
            if scale:
                vals = [round(v * scale) for v in vals]
            else:                                                  # 원장에 없으면 공식집행을 기준월부터 평탄
                vals = [0 if i < li else od['exec'] for i in range(len(mms))]
            pct = [round(v / od['target'] * 100) if od['target'] else 0 for v in vals]   # 대상액대비 집행률(단조증가)
            depts.append({'name': dept, 'values': vals, 'pct': pct, 'target': od['target'], 'goal': od['goal'], 'exec': od['exec']})
        depts.sort(key=lambda d: -d['pct'][-1])
        months = [f'2026-{m}' for m in mms]
        return {'months': months, 'depts': depts}

    return {'asof': asof,
            'sobi': {**axis(sobi_names, off['sobi']['depts']), 'label': '소비투자', 'period': '분기',
                     'total': off['sobi']['total']},
            'sok': {**axis(sok_names, off['sok']['depts']), 'label': '신속집행', 'period': '반기',
                    'total': off['sok']['total']}}


def build_soksok_race(exec_path, stat_path, chug_ym='', rate_sched=None, budget_total=0):
    """무주 재정공개 원장 → 부서별 신속집행 실적률 러닝차트(편성부서 기준, 공식 42통계목).
    실적률 = 누계집행 ÷ (대상액 × 목표율[월]) × 100  (100%=목표 페이스대로 집행).
    대상액은 시간가변(추경월 전=당초·후=현액) + 명세서→예산현액 스코프 보정(budget_total).
    rate_sched = {월:목표율fraction}(1분기 0.1967·상반기 0.556)."""
    rate_sched = rate_sched or {}
    try:
        ex = json.load(open(exec_path, encoding='utf-8')).get('biz', {})
    except FileNotFoundError:
        return None
    if not ex:
        return None
    asof = ''
    try:
        asof = json.load(open(exec_path, encoding='utf-8')).get('asof', '')
    except Exception:
        pass
    # ★ 편성부서 매핑: 원장은 집행부서(면 등)로 잡히지만 목표는 편성부서(본청)라 미스매치 →
    #   집행을 편성부서로 재귀속(muju_tree 매칭과 동일: 정확키 우선, 없으면 사업명→편성부서).
    stat = {}
    try:
        stat = json.load(open(stat_path, encoding='utf-8'))
    except FileNotFoundError:
        pass
    _build_soksok_names(stat)                     # 원장 이름 매칭용 42통계목 이름셋
    biz2dept = {}
    for k in stat:
        dp, bz = (k.split('\x01') + [''])[:2]
        biz2dept.setdefault(_tree_norm(bz), dp)

    def eupbu(k):                                # 원장 키 → 편성부서(목표 있는 사업만; 미매칭=목표없음→None 스킵)
        if k in stat:
            return k.split('\x01')[0]
        return biz2dept.get(_tree_norm(k.split('\x01')[1] if '\x01' in k else k))

    per, allm = {}, set()                        # 편성부서 → {월: 그달 신속집행}
    for k, moks in ex.items():
        dept = eupbu(k)
        if not dept:
            continue
        for mok, nd in moks.items():
            if not is_soksok(mok):
                continue
            for mm, amt in (nd.get('m') or {}).items():
                month = (asof[:4] if asof else '2026') + mm
                per.setdefault(dept, {})[month] = per.setdefault(dept, {}).get(month, 0) + amt
                allm.add(month)
    months = sorted(allm)
    if not months:
        return None
    def sok_target(d):                          # 부서별 신속대상 예산 합(원)
        t = {}
        for k, v in d.items():
            dept = k.split('\x01')[0]
            if not dept:
                continue
            for g in v['groups']:
                for s in g['stats']:
                    if _sok_code(g, s):
                        t[dept] = t.get(dept, 0) + (s['amt'] or 0) * 1000
        return t
    tgt_hy = sok_target(stat)                    # 현액(본예산+추경)
    tgt_da = tgt_hy                              # 당초(본예산). 백업 있으면 사용
    try:
        da = json.load(open(os.path.join(os.path.dirname(stat_path), 'muju_stat_dangcho.json'), encoding='utf-8'))
        tgt_da = sok_target(da)
    except Exception:
        pass
    stat_total = sum((s.get('amt') or 0) * 1000 for v in stat.values() for g in v['groups'] for s in g['stats'])
    scale = (budget_total / stat_total) if (budget_total and stat_total) else 1.0   # 명세서→예산현액 보정(공식 대상액 스코프)
    out = []
    for dept, mv in per.items():
        cum, vals, pct, prog = 0, [], [], []
        for m in months:
            cum += mv.get(m, 0); vals.append(cum)
            base = (tgt_da if (chug_ym and m < chug_ym) else tgt_hy).get(dept, 0) * scale   # 대상액(추경 전=당초·후=현액, 스코프보정)
            r = rate_sched.get(m, 0)                              # 그 달 목표율(누적)
            goal = base * r
            pct.append(round(cum / goal * 100) if goal else 0)   # 실적률 = 집행/목표(100%=페이스대로)
            prog.append(round(cum / base * 100) if base else 0)  # 참고: 대상액 대비 집행률
        out.append({'name': dept, 'values': vals, 'pct': pct, 'prog': prog, 'target': tgt_hy.get(dept, 0)})
    out.sort(key=lambda d: -(d['pct'][-1]))      # 최종 실적률 순
    return {'months': [f'{m[:4]}-{m[4:]}' for m in months], 'depts': out, 'rate_sched': rate_sched,
            'asof': asof, 'chug_ym': chug_ym, 'source': '무주군 재정정보공개(copen.muju.go.kr)'}


def build_muju_tree(stat_path, exec_path, out_path):
    """muju_stat(예산 트리) + muju_exec_biz(집행) → muju_tree.json (통계목에 집행 sp·적요 spd 베이크).
    매칭: (부서\x01사업) 정확 → 사업명만(부서귀속차 흡수). 통계목명은 정규화 비교.
    반환: 집행 asof (없으면 '')."""
    try:
        stat = json.load(open(stat_path, encoding='utf-8'))
    except FileNotFoundError:
        return ''
    try:
        eb = json.load(open(exec_path, encoding='utf-8'))
        ex, asof = eb.get('biz', {}), eb.get('asof', '')
    except FileNotFoundError:
        ex, asof = {}, ''
    idx = {}                                      # 사업명(정규화) → {통계목(정규화): {s, d}}  (부서귀속차 fallback)
    for k, moks in ex.items():
        nb = _tree_norm(k.split('\x01')[1] if '\x01' in k else k)
        dst = idx.setdefault(nb, {})
        for mok, nd in moks.items():
            d = dst.setdefault(_tree_norm(mok), {'s': 0, 'n': 0, 'd': []})
            d['s'] += nd.get('s', 0)
            d['n'] += nd.get('n', 0)
            d['d'].extend(nd.get('d', []))
    for dm in idx.values():
        for d in dm.values():
            d['d'] = sorted(d['d'], key=lambda x: -x[1])[:3]

    def moks_for(dept, biz):
        m = ex.get(dept + '\x01' + biz)
        if m:
            return {_tree_norm(mok): nd for mok, nd in m.items()}
        return idx.get(_tree_norm(biz))

    matched = 0
    for key, entry in stat.items():
        dept, biz = (key.split('\x01') + [''])[:2]
        allst = [s for g in entry.get('groups', []) for s in g.get('stats', [])]
        gmap = {id(s): g for g in entry.get('groups', []) for s in g.get('stats', [])}
        sobi = sum((s.get('amt') or 0) * 1000 for s in allst if _sok_code(gmap[id(s)], s))   # 소비투자(42통계목)
        sok = sum((s.get('amt') or 0) * 1000 for s in allst if s.get('name') in SOK_NAMES)   # 신속집행(36통계목)
        if sobi:
            entry['sobi'] = sobi                   # 소비투자 대상 예산 → 배지
        if sok:
            entry['sok'] = sok                     # 신속집행 대상 예산 → 배지
        em = moks_for(dept, biz)
        if not em:
            continue
        matched += 1
        used = set()
        for g in entry.get('groups', []):
            for s in g.get('stats', []):
                nk = _tree_norm(s['name'])
                nd = em.get(nk)
                if nd:
                    s['sp'] = nd.get('s', 0)
                    if nd.get('n'):
                        s['spn'] = nd['n']            # 지출 건수(적요는 top3만 → "외 N건"용)
                    if nd.get('d'):
                        s['spd'] = nd['d']
                    used.add(nk)
        extra = sum(nd.get('s', 0) for nk, nd in em.items() if nk not in used)
        if extra:
            entry['sp_extra'] = extra
    json.dump(stat, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f"  muju_tree: 예산 {len(stat)}개 세부사업 중 집행 매칭 {matched} · asof {asof or '없음'}")
    return asof


def build_dept_race(home_u, ds, names, fyr, asof):
    """홈 지자체 부서별 '올해 집행액' 월별 경주(1월~기준일 월). 부서별 집행 진도 레이스.
    무주 1개 시군만 월별 조회라 가벼움(캐시 불필요). 현재월은 asof, 지난달은 월말일."""
    A, flt, F = ds['amounts'], ds['filters'], ds['fields']
    y, asof_m = int(fyr), int(asof[4:6])
    months, depts = [], {}
    for m in range(1, asof_m + 1):
        last = calendar.monthrange(y, m)[1]
        ymd = asof if m == asof_m else f'{y}{m:02d}{last:02d}'
        rws = lofin.rows(ds['endpoint'], {flt['year']: fyr, flt['asof']: ymd, flt['unit']: home_u['laf_cd']}, key=ds.get('row_key'))
        months.append(f'{y}-{m:02d}')
        for x in rws:
            nm = names.get(x.get(F['dept']) or '', x.get(F['dept']) or '?')
            depts.setdefault(nm, {})[m] = depts.setdefault(nm, {}).get(m, 0) + _int(x.get(A['spent']))
    out = [{'name': nm, 'values': [mv.get(m, 0) for m in range(1, asof_m + 1)]} for nm, mv in depts.items()]
    out.sort(key=lambda d: -d['values'][-1])
    print(f"  dept_race: {len(out)}개 부서 × {asof_m}개월")
    return {'months': months, 'depts': out}


def build():
    regions = cfg('regions.json')['regions']
    datasets = {d['id']: d for d in cfg('datasets.json')['datasets']}
    dept_map = cfg('dept_map.json', optional=True)
    dept_order = cfg('dept_order.json', optional=True)
    population = cfg('population.json', optional=True)
    pop_map = population.get('pop', {})                    # laf_cd → 주민등록 인구(1인당 지표용)
    indicators = cfg('indicators.json', optional=True)
    bc = cfg('build.json')
    region = next(r for r in regions if r['id'] == bc['region'])
    ds = datasets[bc['datasets'][0]]                       # 1차 = 세출집행(QWGJK)
    fyr = bc['years'][0]
    F, A, flt = ds['fields'], ds['amounts'], ds['filters']
    exports = bc.get('exports', [])
    probe_cd = region['units'][0]['laf_cd']               # 대표 시군(최대규모) = 완전성 판단 기준
    # ★ find_asof = 첫 API 호출(아직 data/ 에 아무것도 안 씀). 여기서 gov 접속이 막히면
    #   기존 커밋된 데이터를 그대로 두고 exit 0 → Actions green, 대시보드 무회귀.
    #   실제 갱신은 한국 IP(로컬 스케줄)에서 돌린다.
    try:
        asof = find_asof(ds, fyr, probe_cd) if bc.get('asof') == 'latest' else bc['asof']
    except _NET_ERRS as e:
        print(f'⚠ lofin(gov) 접속 불가 — 빌드 건너뜀, 기존 data/ 유지: {type(e).__name__} {e}', file=sys.stderr)
        print('  (GitHub 러너 IP 차단 추정. 한국 IP 로컬 스케줄이 실제 갱신 담당)')
        sys.exit(0)
    print(f'기준일(asof) = {asof}')

    ind_map = fetch_indicators(region['wa_laf_cd'], indicators) if indicators else {}

    if 'raw' in exports:
        os.makedirs(os.path.join(ROOT, 'data', 'raw'), exist_ok=True)

    units_out, home, home_name, home_biz = [], None, None, set()
    grant = {}                                            # 사업명 → {field, units:{시군:국도비액}} (시군비교용)
    for u in region['units']:
        rws = lofin.rows(ds['endpoint'], {flt['year']: fyr, flt['asof']: asof, flt['unit']: u['laf_cd']}, key=ds.get('row_key'))

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

        budget, spent = _sum(rws, A['budget']), _sum(rws, A['spent'])   # budget=예산현액(bdg_cash)
        plan = _sum(rws, A['plan']) if A.get('plan') else 0             # 편성(cpl) → 현액-편성 = 이월+추경
        natl, prov, local = _sum(rws, A['natl']), _sum(rws, A['prov']), _sum(rws, A['local'])
        byf = {}
        for x in rws:
            e = byf.setdefault(x.get(F['field']) or '기타', {'budget': 0, 'spent': 0, 'natl': 0, 'prov': 0, 'local': 0})
            e['budget'] += _int(x.get(A['budget']))
            e['spent'] += _int(x.get(A['spent']))
            e['natl'] += _int(x.get(A['natl']))       # 분야별 재원 → 14시군 시군비 비중 비교용
            e['prov'] += _int(x.get(A['prov']))
            e['local'] += _int(x.get(A['local']))
        ind = ind_map.get(u['laf_cd'], {})                # 재정지표(API): 1인당 지방세부담·세출예산(원)
        pop = ind.get('pop') or _int(pop_map.get(u['laf_cd']))  # 인구: API 우선, 없으면 config 폴백
        pc_tax = _int(ind.get('pc_tax'))                  # 1인당 지방세부담(내는 것)
        pc_ebdg = _int(ind.get('pc_ebdg'))                # 1인당 세출예산(받는 것)
        units_out.append({
            'name': u['name'], 'laf_cd': u['laf_cd'], 'type': u['type'], 'home': u.get('home', False),
            'budget': budget, 'spent': spent, 'plan': plan, 'rate': round(spent / budget * 100, 1) if budget else 0,
            'biz_count': len(rws), 'fields': byf,
            'natl': natl, 'prov': prov, 'local': local,
            'natl_rate': round(natl / budget * 100, 1) if budget else 0,
            'pop': pop,
            'pc_budget': round(budget / pop) if pop else 0,   # 1인당 편성(집행 기준)
            'pc_spent': round(spent / pop) if pop else 0,     # 1인당 지출
            'pc_natl': round(natl / pop) if pop else 0,       # 1인당 국비확보
            'pc_local': round(local / pop) if pop else 0,     # 1인당 자체재원(군비)
            'pc_tax': pc_tax,                                 # 1인당 지방세부담(내는 것, 예산기준)
            'pc_ebdg': pc_ebdg,                               # 1인당 세출예산(받는 것, 예산기준)
            'benefit': round(pc_ebdg / pc_tax, 1) if pc_tax else 0,  # 수혜배율=받는것÷내는것
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

    mb = cfg('muju_budget_2026.json', optional=True) or {}   # 무주 예산서 총괄(추경 등, 천원) — home KPI용
    if mb.get('chugyeong'):
        for u in units_out:
            if u.get('home'):
                u['chugyeong'] = mb['chugyeong'] * 1000       # 최근추경 증감(원)
                u['chugyeong_round'] = mb.get('chugyeong_round', '')

    race = build_race(region['units'], ds, units_out, fyr)   # 10개년 시군 예산 경주(과거 캐시)
    _exec = os.path.join(ROOT, 'data', 'muju_exec_biz.json')
    _stat = os.path.join(ROOT, 'data', 'muju_stat.json')
    race2 = build_race2(_exec, _stat, os.path.join(ROOT, 'data', 'muju_exec_official.json'))   # 소비투자·신속집행 2축 월별(공식 보정)
    exec_asof = build_muju_tree(_stat, _exec, os.path.join(ROOT, 'data', 'muju_tree.json'))  # 예산+집행 병합

    summary = {
        'region': region['name'], 'dataset': ds['name'], 'fyr': fyr, 'asof': asof,
        'race': race, 'race2': race2, 'muju_exec_asof': exec_asof,
        'updated': _now_kst().strftime('%Y-%m-%d %H:%M'),
        'pop_asof': population.get('asof'), 'pop_source': population.get('source'),
        'ind_source': indicators.get('source'), 'ind_fyr': indicators.get('fyr'),
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
