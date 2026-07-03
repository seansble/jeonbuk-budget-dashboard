# DATA.md — 데이터 소스 지도 + 유지보수 런북

> 데이터가 5갈래(API 2 · 스크래핑 1 · 파일 3종)에서 들어온다. 뭐가 자동이고 뭐가 수동인지,
> 어디가 깨지기 쉬운지 이 문서 하나로 파악한다. (마지막 갱신 2026-07-03)

## 한눈 파이프라인

```
[자동 · 매일 11:00 KST GitHub Actions]
  lofin365 QWGJK(세출집행) ──┐
  lofin365 BJHJB·HCDIB(지표) ─┼─ build/build.py ──→ data/summary.json ─┐
  copen.muju 원장(D-2 증분) ──┘        │                               ├─→ index.html
                                       └→ data/muju_tree.json ─────────┤   (이 3개만 로드)
[수동 · 이벤트 트리거]                                                  │
  무주 예산서 PDF ──→ build_stat.py ──→ data/muju_stat.json ────────────┘
  추경 사업명세서 PDF → build_chugyeong.py → muju_stat.json 현액 갱신
  재정집행 종합엑셀  → parse_official.py → muju_exec_official.json (러닝차트 공식앵커)
  지방세통계연감    → build_tax.py → jeonbuk_tax.json (세목 5개년, 프론트 미연결)
```

## 소스별 상세

| 소스 | 갱신 | 스크립트 | 출력 | 비고 |
|---|---|---|---|---|
| **lofin365 QWGJK** (세부사업별 세출집행) | 자동·일간 | `build.py` (`lofin.py`) | `summary.json`, `data/raw/*.json` | 키=`LOFIN_KEY`(.env / GitHub Secret). `row_key` dedup 필수 |
| **lofin365 BJHJB/HCDIB** (1인당 지방세·세출) | 자동·연간값 | `build.py fetch_indicators` | summary 내 + `indicators_cache.json`(실패 폴백) | 연간인데 매일 호출(낭비, 알려진 부채) |
| **copen.muju.go.kr 원장** (건별 지출) | 자동·**D-2 증분** | `muju_exec.py inc` | `muju_exec_biz.json` | 게시가 ~하루 늦음 → 그제까지만 수집. TLS SECLEVEL=1 |
| **무주 예산서 PDF** (당초, muju.go.kr) | 수동·연 1회 | `build_stat.py` | `muju_stat.json` (+`_dangcho` 백업) | 사업명세서만 통계목 있음(세출총괄 X). 단위 천원 |
| **추경 사업명세서 PDF** | 수동·추경마다 | `build_chugyeong.py` | `muju_stat.json` 통계목 현액 덮어쓰기 | `muju_budget_2026.json`(총괄·성립월)도 같이 갱신 |
| **재정집행 종합엑셀** (기획조정실) | 수동·스냅샷마다 | `parse_official.py` | `muju_exec_official.json` | 러닝차트 공식앵커. 현재 3.17 한 장 — **6월말 자료가 다음 앵커** |
| **지방세통계연감** (행안부 파일) | 수동·연 1회 | `build_tax.py` | `jeonbuk_tax.json` | 14시군 세목 실적 2019~2023. 단위 확인 |
| **인구** (전북도청 주민등록) | 수동·연 1회 | config 직접 수정 | `config/population.json` | API(BJHJB pptn_num)가 우선, 이건 폴백 |

## 수동 갱신 런북 (이벤트 → 할 일)

- **2회 추경 성립** → 추경 사업명세서 PDF 다운 → `python build/build_chugyeong.py` → `config/muju_budget_2026.json`의 `chugyeong*`·`chugyeong_ym` 갱신 → 커밋
- **새 재정집행 엑셀 입수(6월말 등)** → Downloads에 두고 `python build/parse_official.py` → 러닝차트에 2번째 앵커/2분기 목표 반영 가능(프론트 확장 필요) → 커밋
- **새 지방세통계연감(매년 말)** → `python build/build_tax.py` → 커밋
- **연초(새 회계연도)** → `regions/datasets/build.json` 연도, 예산서 PDF 재파싱, population 갱신
- ※ PDF·엑셀 산출물은 Actions가 안 만짐(로컬 전용) — **json을 커밋해야 배포됨**

## ⚠️ 함정 모음 (전부 실측으로 배운 것 — 재발 주의)

1. **gov 스냅샷은 아침에 채워진다**: D일 스냅샷은 D+1 아침 ~10시(KST) 완성. 그 전에 fetch하면 전날 복사본/미완본. → cron 11:00 KST + `find_asof` 완전성 가드(행수 급감 스킵)
2. **페이지네이션 중복**: gov가 채우는 중에 fetch하면 페이지 사이 행이 밀려 같은 사업 2행 유입(금액만 미묘차) → 14시군 +9,654억 부풀림 실측. → `datasets.json row_key` + `lofin.rows(key=)` dedup. 행수 가드는 감소만 잡으므로 dedup 필수
3. **과거 스냅샷도 소급 갱신됨**: 0701 값이 다음날 +4.2억 자람. "한 번 받은 날짜=불변" 아님
4. **원장 게시 지연**: 지출→사이트 게시가 ~하루 늦음. 당일 크롤분이 `days` 가드에 박제되면 영구 부분집계(07월 +82% 누락 실측) → 수집은 **그제(D-2)까지만**
5. **lofin 일시 timeout**: 재시도 3회(5s/10s) 있음. 아침 9시대는 시간대 자체가 나쁨 — 그 시간 수동 빌드 금지
6. **TLS**: lofin·muju 둘 다 `SECLEVEL=1` 필요 (구형 암호)
7. **CI는 UTC**: 시각 표시는 반드시 `_now_kst()` (과거 9h 밀림 사고)
8. **단위 3종 혼재**: QWGJK=원 · muju_stat(예산서)=천원 · 종합엑셀=백만원. 병합 시 변환 확인
9. **API 예산 필드**: `cpl_amt`=최종예산(당초+추경, 당초 아님!) · `bdg_cash_amt`=예산현액(최종+이월). 집행률 분모는 현액
10. **러닝차트 공식앵커**: 3.17 엑셀 기준 원장을 부서별 스케일 보정. 빌드 후 "03월 러닝합 == 공식 exec" 오차 0 확인이 회귀 게이트

## 파일 사전

```
config/  regions(14시군) · datasets(QWGJK+row_key) · build(연도·asof) · dept_map(무주 부서코드→명)
         dept_order(직제순) · population(인구 폴백) · indicators(BJHJB/HCDIB) · muju_budget_2026(추경 총괄)
data/    summary.json        ← 허브(units·race·race2·home) — 프론트 메인
         muju_tree.json      ← 예산(muju_stat)+집행(muju_exec_biz) 병합 — 통계목 드릴다운
         muju_stat.json      ← 예산서 통계목 트리(현액) / muju_stat_dangcho.json = 당초 백업(추경 전 대상액 계산용)
         muju_exec_biz.json  ← 원장 아카이브 {부서\x01사업: {통계목: {s,n,m월별,d적요}}} + days/months_done
         muju_exec_official.json ← 공식 3.17 스냅샷(소비투자/신속집행 부서별 대상·목표·집행)
         jeonbuk_tax.json    ← 세목 5개년(프론트 미연결) · indicators_cache.json ← 지표 폴백
         race_cache.json     ← 10개년 경주 과거연도 캐시 · raw/ ← QWGJK 원본(시군별)
```
