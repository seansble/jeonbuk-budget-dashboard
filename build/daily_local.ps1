# 로컬(한국 IP) 일일 빌드+푸시 — GitHub 러너가 gov API(.go.kr)에 못 닿는 문제 우회.
# Windows 작업 스케줄러가 매일 이 스크립트를 실행한다(등록: build/register_task.ps1).
# gov 는 전날(D-1) 세출 스냅샷을 09~10시 KST 에 채우므로 그 이후(기본 11시)에 돈다.
#
# 하는 일: git pull → muju 원장 증분(best-effort) → build.py → 변경 있으면 commit+push.
# 로그: build/daily_local.log (최근 실행 흔적, KST 타임스탬프).

$ErrorActionPreference = 'Continue'
# python 한글 출력을 UTF-8로 캡처(스케줄러 기본 cp949면 로그가 깨짐)
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$repo = Split-Path -Parent $PSScriptRoot          # build/ 의 부모 = 레포 루트
$log  = Join-Path $PSScriptRoot 'daily_local.log'

function Log($msg) {
    $ts = (Get-Date).ToUniversalTime().AddHours(9).ToString('yyyy-MM-dd HH:mm:ss')  # KST
    $line = "$ts  $msg"
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}

Set-Location $repo
Log "=== daily_local 시작 (repo=$repo) ==="

# 0) 원격 최신 반영(Actions 방어커밋/수동커밋과 충돌 방지)
git pull --ff-only 2>&1 | ForEach-Object { Log "pull: $_" }

# 1) 무주 재정공개 원장 증분 — 실패해도 계속(기존 muju_exec_biz.json 유지)
try {
    python build/muju_exec.py inc 2>&1 | Select-Object -Last 3 | ForEach-Object { Log "muju_inc: $_" }
} catch { Log "muju_inc 예외(무시): $_" }

# 2) 메인 빌드 — 한국 IP 라 gov 접속 정상. 실패 시 커밋하지 않음.
python build/build.py 2>&1 | Select-Object -Last 6 | ForEach-Object { Log "build: $_" }
if ($LASTEXITCODE -ne 0) {
    Log "build.py 실패(exit=$LASTEXITCODE) — 커밋 생략, 종료"
    exit 1
}

# 3) 변경 있으면 커밋+푸시
git add data exports 2>&1 | Out-Null
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Log "변경 없음 — 커밋 생략"
} else {
    $d = (Get-Date).ToUniversalTime().AddHours(9).ToString('yyyy-MM-dd')
    git commit -m "data: 자동 갱신 ($d, 로컬 스케줄)" 2>&1 | ForEach-Object { Log "commit: $_" }
    git push 2>&1 | ForEach-Object { Log "push: $_" }
}
Log "=== daily_local 종료 ==="
