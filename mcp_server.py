"""전북 재정·경제 대시보드 MCP 서버 — 질문단위 tool 6종.

데이터는 공개 레포의 raw.githubusercontent 에서 라이브로 읽는다(로컬 의존 0).
→ 어느 PC에서 띄우든 대시보드 최신 데이터를 그대로 질의. 캐시 TTL 5분.

실행:  python mcp_server.py            (stdio MCP 서버)
개발:  JEONBUK_LOCAL=1 python ...      (레포 data/ 로컬 파일 사용, 오프라인)

Claude Desktop 등록(claude_desktop_config.json):
  "mcpServers": {
    "jeonbuk": { "command": "python",
                 "args": ["C:\\\\Users\\\\PC_1M\\\\Desktop\\\\jeonbuk-budget-dashboard\\\\mcp_server.py"] }
  }
"""
import os
import json
import time
import urllib.request

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("jeonbuk-finance")

BASE = "https://raw.githubusercontent.com/seansble/jeonbuk-budget-dashboard/main/data/"
_HERE = os.path.dirname(os.path.abspath(__file__))
_cache = {}          # name -> (ts, data)


def _load(name, ttl=300):
    """data/<name> 을 raw github(또는 JEONBUK_LOCAL 시 로컬)에서 읽고 5분 캐시."""
    now = time.time()
    hit = _cache.get(name)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if os.environ.get("JEONBUK_LOCAL"):
        with open(os.path.join(_HERE, "data", name), encoding="utf-8") as f:
            data = json.load(f)
    else:
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "jeonbuk-mcp"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    _cache[name] = (now, data)
    return data


def _eok(x):
    """원 → 사람이 읽기 쉬운 문자열(억/만원/원)."""
    try:
        x = int(x)
    except Exception:
        return str(x)
    if abs(x) >= 100_000_000:
        return f"{round(x / 1e8):,}억"
    if abs(x) >= 10_000:
        return f"{round(x / 1e4):,}만원"
    return f"{x:,}원"


def _resolve(summary, q):
    """'무주'·'무주군'·laf_cd 아무거나 → unit dict. 못 찾으면 None."""
    q = (q or "").strip()
    units = summary["units"]
    for u in units:                                   # 정확 매칭 우선
        if q in (u["laf_cd"], u["name"]) or u["name"].rstrip("시군") == q.rstrip("시군"):
            return u
    for u in units:                                   # 부분 매칭
        if q and (q in u["name"] or u["name"].startswith(q)):
            return u
    return None


# 랭킹/비교에 쓰는 지표 — 한글·영문 키 모두 허용
_METRICS = {
    "rate": ("rate", "집행률(%)", False), "집행률": ("rate", "집행률(%)", False),
    "natl": ("natl", "국비 확보액", True), "국비": ("natl", "국비 확보액", True),
    "natl_rate": ("natl_rate", "국비 비중(%)", False), "국비비중": ("natl_rate", "국비 비중(%)", False),
    "budget": ("budget", "예산현액(편성 규모)", True), "편성": ("budget", "예산현액(편성 규모)", True),
    "spent": ("spent", "집행액", True), "집행": ("spent", "집행액", True),
    "pc_budget": ("pc_budget", "1인당 예산", True), "1인당예산": ("pc_budget", "1인당 예산", True),
    "pc_tax": ("pc_tax", "1인당 지방세(세부담)", True), "세부담": ("pc_tax", "1인당 지방세(세부담)", True),
    "benefit": ("benefit", "수혜배율(받는것÷내는것)", False), "수혜배율": ("benefit", "수혜배율(받는것÷내는것)", False),
    "pop": ("pop", "인구(명)", False), "인구": ("pop", "인구(명)", False),
}


@mcp.tool()
def jeonbuk_overview() -> dict:
    """전북 14개 시군 재정·경제 대시보드 개요. 기준일·갱신시각과 14시군의 편성/집행/집행률/국비비중 요약 리스트를 반환한다.
    '전북 대시보드 최신 상태', '언제 기준이야', '시군 목록' 같은 질문에 사용."""
    s = _load("summary.json")
    rows = sorted(
        ({"name": u["name"], "type": u["type"], "집행률": u["rate"],
          "편성": _eok(u["budget"]), "집행": _eok(u["spent"]),
          "국비비중": u["natl_rate"], "인구": u["pop"], "home": u.get("home", False)}
         for u in s["units"]),
        key=lambda x: -x["집행률"])
    return {
        "지역": s["region"], "회계연도": s["fyr"],
        "세출집행 기준일(QWGJK)": s["asof"],
        "무주원장 기준일": s.get("muju_exec_asof"),
        "갱신시각(KST)": s["updated"],
        "인구 기준": s.get("pop_asof"),
        "시군수": len(s["units"]),
        "시군요약(집행률순)": rows,
    }


@mcp.tool()
def region_finance(region: str) -> dict:
    """한 시군의 재정 상세. region = 시군 이름('무주'/'무주군') 또는 laf_cd.
    편성(예산현액)·집행·집행률·국비/도비/시군비·국비비중·인구·1인당(예산/집행/국비/지방세/세출)·수혜배율·사업수·분야별 상위를 반환한다."""
    s = _load("summary.json")
    u = _resolve(s, region)
    if not u:
        return {"error": f"'{region}' 시군을 못 찾음", "가능한값": [x["name"] for x in s["units"]]}
    fields = u.get("fields", {})                      # {분야명: {budget,spent,natl,...}}
    top = sorted(fields.items(), key=lambda x: -x[1].get("budget", 0))[:8]
    return {
        "시군": u["name"], "laf_cd": u["laf_cd"], "유형": u["type"],
        "편성_예산현액": u["budget"], "편성_읽기": _eok(u["budget"]),
        "집행": u["spent"], "집행_읽기": _eok(u["spent"]), "집행률(%)": u["rate"],
        "국비": u["natl"], "도비": u["prov"], "시군비": u["local"], "국비비중(%)": u["natl_rate"],
        "인구": u["pop"],
        "1인당": {"예산": u["pc_budget"], "집행": u["pc_spent"], "국비": u["pc_natl"],
                  "지방세_세부담": u["pc_tax"], "세출예산": u["pc_ebdg"]},
        "수혜배율(받는÷내는)": u["benefit"],
        "사업수": u["biz_count"],
        "분야별_상위": [{"분야": nm, "편성": _eok(v["budget"]), "집행": _eok(v["spent"]),
                       "국비": _eok(v.get("natl", 0))} for nm, v in top],
    }


@mcp.tool()
def compare_regions(metric: str = "rate", top: int = 14) -> dict:
    """14개 시군을 한 지표로 줄세운 랭킹. metric = rate(집행률)·natl(국비)·natl_rate(국비비중)·
    budget(편성규모)·spent(집행)·pc_budget(1인당예산)·pc_tax(1인당지방세=세부담)·benefit(수혜배율)·pop(인구).
    '어디가 집행률 높아', '국비 제일 많이 받은 곳', '1인당 예산 순위' 같은 비교 질문에 사용."""
    s = _load("summary.json")
    m = _METRICS.get(metric.strip())
    if not m:
        return {"error": f"모르는 지표 '{metric}'", "가능한값": sorted({v[0] for v in _METRICS.values()})}
    key, label, is_won = m
    rows = sorted(s["units"], key=lambda u: -(u.get(key) or 0))[:max(1, top)]
    out = []
    for i, u in enumerate(rows, 1):
        val = u.get(key)
        out.append({"순위": i, "시군": u["name"],
                    "값": _eok(val) if is_won else val,
                    "원값": val})
    return {"지표": label, "기준일": s["asof"], "랭킹": out}


@mcp.tool()
def muju_departments(top: int = 28) -> dict:
    """무주군 부서별 재정 — 28개 부서의 편성/집행/집행률/재원(국·도·시군비)을 편성 규모순으로.
    무주는 예산서 매핑이 돼 있어 부서 드릴다운이 가능한 유일한 시군.
    '무주 어느 부서 예산 커', '집행률 낮은 부서' 같은 질문에 사용."""
    s = _load("summary.json")
    depts = s["home"]["depts"]
    rows = sorted(depts, key=lambda d: -d["budget"])[:max(1, top)]
    return {
        "시군": s["home"]["name"], "기준일": s["asof"], "부서수": len(depts),
        "부서별": [{"부서": d["name"], "편성": _eok(d["budget"]), "집행": _eok(d["spent"]),
                   "집행률(%)": d["rate"], "국비": _eok(d["natl"]), "시군비": _eok(d["local"]),
                   "사업수": d["count"]} for d in rows],
    }


@mcp.tool()
def muju_business(query: str, limit: int = 20) -> dict:
    """무주군 세부사업 검색 — 이름에 query가 들어간 세부사업을 찾아 부서·분야·편성·집행·집행률·재원을 반환.
    '무주 청년 사업', '축제 예산', 'OO 사업 집행률' 같은 질문에 사용. query='' 이면 편성 큰 사업 상위."""
    s = _load("summary.json")
    q = (query or "").strip()
    hits = []
    for d in s["home"]["depts"]:
        for b in d.get("biz", []):
            nm = b.get("biz", "")
            if not q or q in nm:
                bg, sp = b.get("budget", 0), b.get("spent", 0)
                hits.append({"세부사업": nm, "부서": d["name"], "분야": b.get("field", ""),
                             "편성": bg, "편성_읽기": _eok(bg), "집행": sp, "집행_읽기": _eok(sp),
                             "집행률(%)": round(sp / bg * 100, 1) if bg else 0,
                             "국비": b.get("natl", 0), "도비": b.get("prov", 0), "시군비": b.get("local", 0)})
    hits.sort(key=lambda x: -x["편성"])
    return {"검색어": q or "(전체)", "찾음": len(hits), "결과": hits[:max(1, limit)]}


@mcp.tool()
def tax_trend(region: str, kind: str = "") -> dict:
    """시군 세목별 세금·소득 추이(지방세통계연감, 2019~2024). region=시군, kind=세목(지방소득세·취득세·재산세·주민세·자동차세·담배소비세 등).
    지방소득세=소득 프록시, 취득세/재산세=부동산, 자동차세/담배소비세=소비. kind 생략 시 최신년 전체 세목.
    '무주 지방소득세 추이', '취득세 어디가 많아' 같은 경제 렌즈 질문에 사용."""
    tx = _load("jeonbuk_tax.json")
    s = _load("summary.json")
    u = _resolve(s, region)
    if not u:
        return {"error": f"'{region}' 시군을 못 찾음", "가능한값": [x["name"] for x in s["units"]]}
    laf = u["laf_cd"]
    years = sorted(tx.keys())
    kind = (kind or "").strip()
    # 사용 가능한 세목 목록(최신년 기준)
    latest = tx[years[-1]].get(laf, {})
    세목목록 = [k for k in latest if k != "name"]
    if not kind:
        return {"시군": u["name"], "연도": years[-1],
                "세목목록": 세목목록,
                "최신년_세목별(원)": {k: latest[k] for k in 세목목록}}
    if kind not in 세목목록:
        return {"error": f"모르는 세목 '{kind}'", "가능한값": 세목목록}
    series = []
    for y in years:
        rec = tx[y].get(laf, {})
        v = rec.get(kind)
        if v is not None:
            series.append({"연도": y, "세액": v, "읽기": _eok(v) if v >= 1e8 else f"{v:,}원",
                           "1인당": round(v / u["pop"]) if u.get("pop") else None})
    return {"시군": u["name"], "세목": kind, "추이": series,
            "설명": "지방소득세=소득·취득세/재산세=부동산·자동차세/담배소비세=소비 프록시"}


@mcp.tool()
def muju_spending(query: str, limit: int = 10) -> dict:
    """무주군 실제 사용내역(적요) — 세부사업에 실제로 무슨 돈이 나갔는지를 통계목별 집행액·건수와
    적요(무엇에 썼나, 대표 top3)로 반환한다. `muju_business`가 편성/집행 '요약'이라면 이건 '실제 지출 내역'.
    '무주 청년내일저축계좌 뭐에 썼어', 'OO사업 사용내역', '축제에 무슨 돈 나갔어' 같은 질문에 사용.
    출처=무주군 재정정보공개 원장(copen.muju.go.kr), 개인정보는 (**) 마스킹. query='' 이면 집행 큰 사업 상위."""
    d = _load("muju_exec_biz.json")
    biz = d.get("biz", {})
    q = (query or "").strip()
    hits = []
    for k, moks in biz.items():
        dept, _, name = k.partition("\x01")
        if q and q not in name and q not in dept:
            continue
        total = sum(nd.get("s", 0) for nd in moks.values())
        moklist = sorted(moks.items(), key=lambda x: -x[1].get("s", 0))
        hits.append({
            "세부사업": name, "부서": dept,
            "총집행": total, "총집행_읽기": _eok(total), "통계목수": len(moks),
            "통계목별": [{
                "통계목": mok, "집행": nd.get("s", 0), "집행_읽기": _eok(nd.get("s", 0)),
                "건수": nd.get("n", 0),
                "사용내역": [{"적요": t, "금액": a} for t, a in nd.get("d", [])],
            } for mok, nd in moklist],
        })
    hits.sort(key=lambda x: -x["총집행"])
    out = {"검색어": q or "(전체)", "찾음": len(hits),
           "원장기준일": d.get("asof"), "결과": hits[:max(1, limit)]}
    if not q:
        out["안내"] = "검색어 없음 → 집행 큰 세부사업 상위"
    return out


@mcp.tool()
def muju_department(name: str = "", limit: int = 20) -> dict:
    """무주군 부서별 지출 정리 — 원장 집행 기준. name 없으면 28개 부서를 총집행순으로 나열,
    name 주면 그 부서의 통계목별 지출(무슨 항목에 썼나)과 세부사업 목록(집행순)을 반환한다.
    'muju_spending'이 세부사업의 적요(사용내역)라면, 이건 부서 단위 집계·구성. 출처=원장(copen.muju.go.kr)."""
    d = _load("muju_exec_biz.json")
    biz = d.get("biz", {})
    q = (name or "").strip()
    # 부서 → 누적
    dept_tot, dept_cnt, dept_biz = {}, {}, {}
    dept_mok = {}                                     # 부서 → {통계목: [집행합, 건수]}
    for k, moks in biz.items():
        dept, _, bname = k.partition("\x01")
        s = sum(nd.get("s", 0) for nd in moks.values())
        c = sum(nd.get("n", 0) for nd in moks.values())
        dept_tot[dept] = dept_tot.get(dept, 0) + s
        dept_cnt[dept] = dept_cnt.get(dept, 0) + c
        dept_biz.setdefault(dept, []).append((bname, s, c))
        if q and dept == q:
            mm = dept_mok.setdefault(dept, {})
            for mok, nd in moks.items():
                e = mm.setdefault(mok, [0, 0])
                e[0] += nd.get("s", 0); e[1] += nd.get("n", 0)
    if not q:
        rows = sorted(({"부서": dp, "총집행": t, "총집행_읽기": _eok(t),
                        "건수": dept_cnt[dp], "세부사업수": len(dept_biz[dp])}
                       for dp, t in dept_tot.items()), key=lambda x: -x["총집행"])
        return {"원장기준일": d.get("asof"), "부서수": len(rows), "부서별": rows}
    if q not in dept_tot:
        return {"error": f"'{q}' 부서 없음", "가능한값": sorted(dept_tot, key=lambda x: -dept_tot[x])}
    moks = sorted(dept_mok.get(q, {}).items(), key=lambda x: -x[1][0])
    bizs = sorted(dept_biz[q], key=lambda x: -x[1])[:max(1, limit)]
    return {
        "부서": q, "원장기준일": d.get("asof"),
        "총집행": dept_tot[q], "총집행_읽기": _eok(dept_tot[q]),
        "건수": dept_cnt[q], "세부사업수": len(dept_biz[q]),
        "통계목별": [{"통계목": mok, "집행": s, "집행_읽기": _eok(s), "건수": n} for mok, (s, n) in moks],
        "세부사업": [{"세부사업": bn, "집행": s, "집행_읽기": _eok(s), "건수": c} for bn, s, c in bizs],
    }


if __name__ == "__main__":
    mcp.run()
