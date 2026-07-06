# 전북특별자치도 세출예산 집행 대시보드

[지방재정365(lofin) OpenAPI](https://www.lofin365.go.kr)로 **전북특별자치도 14개 시군**의 세출예산 집행 현황을 매일 자동으로 갱신하는 대시보드.

## 내용
- **무주군 상세** — 부서별 · 분야별 · 세부사업별 집행
- **전북 14개 시군 비교** — 집행률 · 분야 비중 · 규모 순위

## 데이터
- 출처: 행정안전부 지방재정365 (세부사업별 세출현황 `QWGJK`, 일간 적재)
- 갱신: GitHub Actions가 매일 데이터를 빌드 → GitHub Pages 배포
- 데이터 라이선스: 출처표시, 상업·비상업 이용가능, 2차적 저작물 작성 가능

## 유지보수
- **[DATA.md](DATA.md)** — 데이터 소스 지도 · 수동 갱신 런북 · 함정 모음 (유지보수는 이 문서부터)

## 구조
- `index.html` — 대시보드 (정적, 브라우저에서 `data/`만 읽음)
- `data/` — 매일 빌드되는 집계 JSON (API 키 노출 0)
- `.github/workflows/` — 매일 데이터 빌드 워크플로 (키는 GitHub Secret)
- `mcp_server.py` — MCP 서버 (AI가 대시보드 데이터를 직접 질의)

## MCP 서버 (AI 연동)
`mcp_server.py` 를 띄우면 Claude Desktop 등 AI가 대시보드 데이터를 **질문단위 tool**로 직접 조회한다.
데이터는 공개 레포의 raw.githubusercontent 에서 라이브로 읽어(로컬 의존 0) 5분 캐시.

**tool 8종**: `jeonbuk_overview`(개요·기준일) · `region_finance`(시군 재정 상세) ·
`compare_regions`(지표별 14시군 랭킹) · `muju_departments`(무주 부서 편성/집행 요약) ·
`muju_department`(무주 **부서별 지출 정리** = 통계목·세부사업 구성) ·
`muju_business`(무주 세부사업 요약) · `muju_spending`(무주 **실제 사용내역·적요** = 뭐에 썼나) ·
`tax_trend`(세목별 세금·소득 추이)

```bash
pip install mcp
python mcp_server.py                 # stdio 서버
JEONBUK_LOCAL=1 python mcp_server.py # 개발: 로컬 data/ 사용(오프라인)
```

Claude Desktop `claude_desktop_config.json`:
```json
{ "mcpServers": {
    "jeonbuk": { "command": "python", "args": ["<절대경로>/mcp_server.py"] } } }
```

## 자치단체 코드 (lofin laf_cd)
| 구분 | 코드 |
|---|---|
| 시 | 전주 4511 · 군산 4512 · 익산 4513 · 정읍 4514 · 남원 4515 · 김제 4516 |
| 군 | 완주 4571 · 진안 4572 · **무주 4573** · 장수 4574 · 임실 4575 · 순창 4576 · 고창 4577 · 부안 4578 |
| 도 본청 | 4500 |

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
