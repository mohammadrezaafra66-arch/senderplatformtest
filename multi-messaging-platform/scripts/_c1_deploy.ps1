$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$GatePath = Join-Path $Root "reports\rubika-remediation\C1_SOURCE_AUDIT_GATES.json"
$ReportDir = Join-Path $Root "reports\rubika-remediation"

function Get-Sha256Lower([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLower()
}

function Assert-True([bool]$Value, [string]$Name) {
    if (-not $Value) {
        Write-Host "REFUSE: $Name=False"
        exit 3
    }
    Write-Host "$Name=True"
}

Write-Host "C1_DEPLOY_MODE=backend_frontend_consumers_only"
Write-Host "C1_NOTE=source_audit_must_already_exist"

if (-not (Test-Path $GatePath)) {
    Write-Host "REFUSE: missing C1_SOURCE_AUDIT_GATES.json"
    exit 3
}

$gates = Get-Content -Raw -Path $GatePath | ConvertFrom-Json
Assert-True ([bool]$gates.C1_SOURCE_AUDIT_PASS) "C1_SOURCE_AUDIT_PASS"
Assert-True ([bool]$gates.C1_DEPLOY_SCOPE_EXACT) "C1_DEPLOY_SCOPE_EXACT"
Assert-True (-not [bool]$gates.OTP_PATH_PRESENT) "OTP_PATH_ABSENT"
Assert-True (-not [bool]$gates.MESSAGE_PATH_PRESENT) "MESSAGE_PATH_ABSENT"
Assert-True (-not [bool]$gates.DB_MIGRATION_PATH_PRESENT) "DB_MIGRATION_ABSENT"

$elig = Join-Path $Root "core_engine\services\campaign_sender_eligibility.py"
$deploy = Join-Path $Root "scripts\_c1_deploy.ps1"
$eligHash = Get-Sha256Lower $elig
$deployHash = Get-Sha256Lower $deploy
$meta = $gates.C1_AUDITED_FILE_META
$eligLock = ($eligHash -eq [string]$meta.'core_engine/services/campaign_sender_eligibility.py'.SHA256_AUDITED)
$deployLock = ($deployHash -eq [string]$meta.'scripts/_c1_deploy.ps1'.SHA256_AUDITED)
Assert-True $eligLock "C1_ELIG_HASH_LOCK_PASS"
Assert-True $deployLock "C1_DEPLOY_HASH_LOCK_PASS"
Assert-True ($eligLock -and $deployLock) "C1_HASH_LOCK_PASS"

Write-Host "STEP=readonly_eligibility_simulation"
docker exec -w /app -e PYTHONPATH=/app mmp_core_api python scripts/_c1_readonly_eligibility_sim.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker cp mmp_core_api:/app/reports/rubika-remediation/C1_CAMPAIGN_SENDER_ELIGIBILITY_MATRIX.json (Join-Path $ReportDir "C1_CAMPAIGN_SENDER_ELIGIBILITY_MATRIX.json")
docker cp mmp_core_api:/app/reports/rubika-remediation/C1_CAMPAIGN_UI_MISMATCH_MATRIX.json (Join-Path $ReportDir "C1_CAMPAIGN_UI_MISMATCH_MATRIX.json")

Write-Host "C1_PREDEPLOY_ALL_GATES_PASS=True"
Write-Host "STEP=restart_core_api"
docker restart mmp_core_api | Out-Null
Start-Sleep -Seconds 8
docker exec mmp_core_api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5); print('core_api_healthy')"

Write-Host "STEP=rebuild_frontend"
$composeDir = $Root
Push-Location $composeDir
docker compose build frontend
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
docker compose up -d --no-deps frontend
Pop-Location
Start-Sleep -Seconds 5

Write-Host "STEP=postdeploy_health"
docker exec mmp_core_api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5); print('core_api_healthy')"
docker inspect -f '{{.State.Health.Status}}' mmp_frontend 2>$null
docker inspect -f '{{.State.Status}}' mmp_rubika_worker

Write-Host "C1_DEPLOY_EXIT=0"
Write-Host "MESSAGE_SENT=False"
Write-Host "OTP_REQUESTED=False"
exit 0
