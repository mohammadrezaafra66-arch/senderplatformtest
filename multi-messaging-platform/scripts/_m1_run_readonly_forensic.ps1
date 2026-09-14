$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$GatePath = Join-Path $Root "reports\rubika-remediation\M1_SOURCE_AUDIT_GATES.json"
$ReportDir = Join-Path $Root "reports\rubika-remediation"
$ScriptRel = "scripts\_m1_manual_review_forensic_audit.py"
$ProbeRel = "scripts\_m1_forensic_auth_probe.py"
$SentinelRel = "scripts\_m1_pre_audit_sentinels.py"
$RunnerRel = "scripts\_m1_run_readonly_forensic.ps1"

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

function Assert-False([bool]$Value, [string]$Name) {
    if ($Value) {
        Write-Host "REFUSE: $Name=True"
        exit 3
    }
    Write-Host "$Name=False"
}

if (-not (Test-Path $GatePath)) {
    Write-Host "REFUSE: missing M1_SOURCE_AUDIT_GATES.json - regenerate via scripts/_m1_source_audit_gates.py first"
    exit 3
}

$gates = Get-Content -Raw -Path $GatePath | ConvertFrom-Json

Assert-True ([bool]$gates.M1_SOURCE_AUDIT_PASS) "M1_SOURCE_AUDIT_PASS"
Assert-True ([bool]$gates.M1_SENTINEL_SOURCE_AUDIT_PASS) "M1_SENTINEL_SOURCE_AUDIT_PASS"
Assert-True ([bool]$gates.M1_RUNNER_SOURCE_AUDIT_PASS) "M1_RUNNER_SOURCE_AUDIT_PASS"
Assert-True ([bool]$gates.M1_ALL_EXECUTED_CUSTOM_FILES_AUDITED) "M1_ALL_EXECUTED_CUSTOM_FILES_AUDITED"
Assert-True ([bool]$gates.DB_READ_ONLY_ENFORCED) "DB_READ_ONLY_ENFORCED"
Assert-False ([bool]$gates.DB_WRITE_PATH_PRESENT) "DB_WRITE_PATH_PRESENT"
Assert-True ([bool]$gates.REDIS_READ_ONLY) "REDIS_READ_ONLY"
Assert-False ([bool]$gates.REDIS_MUTATION_PATH_PRESENT) "REDIS_MUTATION_PATH_PRESENT"
Assert-True ([bool]$gates.RUBIKA_AUTH_PROBE_READ_ONLY) "RUBIKA_AUTH_PROBE_READ_ONLY"
Assert-False ([bool]$gates.RUBIKA_AUTH_PROBE_PERSISTS_SESSION) "RUBIKA_AUTH_PROBE_PERSISTS_SESSION"
Assert-False ([bool]$gates.RUBIKA_AUTH_PROBE_REQUESTS_OTP) "RUBIKA_AUTH_PROBE_REQUESTS_OTP"
Assert-False ([bool]$gates.RUBIKA_AUTH_PROBE_WRITES_DB) "RUBIKA_AUTH_PROBE_WRITES_DB"
Assert-False ([bool]$gates.RUBIKA_AUTH_PROBE_WRITES_REDIS) "RUBIKA_AUTH_PROBE_WRITES_REDIS"
Assert-False ([bool]$gates.SESSION_MUTATION_PATH_PRESENT) "SESSION_MUTATION_PATH_PRESENT"
Assert-False ([bool]$gates.IDENTITY_MUTATION_PATH_PRESENT) "IDENTITY_MUTATION_PATH_PRESENT"
Assert-False ([bool]$gates.LOGIN_MUTATION_PATH_PRESENT) "LOGIN_MUTATION_PATH_PRESENT"
Assert-False ([bool]$gates.MESSAGE_MUTATION_PATH_PRESENT) "MESSAGE_MUTATION_PATH_PRESENT"
Assert-False ([bool]$gates.WORKER_MUTATION_PATH_PRESENT) "WORKER_MUTATION_PATH_PRESENT"
Assert-False ([bool]$gates.CONFIG_MUTATION_PATH_PRESENT) "CONFIG_MUTATION_PATH_PRESENT"
Assert-False ([bool]$gates.POOL_MUTATION_PATH_PRESENT) "POOL_MUTATION_PATH_PRESENT"
Assert-True ([bool]$gates.REPORT_WRITE_SCOPE_EXACT) "REPORT_WRITE_SCOPE_EXACT"

$targetIds = @($gates.FORENSIC_TARGET_IDS)
if (($targetIds.Count -ne 5) -or ($targetIds[0] -ne 2) -or ($targetIds[1] -ne 12) -or ($targetIds[2] -ne 19) -or ($targetIds[3] -ne 81) -or ($targetIds[4] -ne 92)) {
    Write-Host "REFUSE: FORENSIC_TARGET_IDS mismatch"
    Write-Host ("got=" + ($targetIds -join ","))
    exit 3
}
Write-Host "FORENSIC_TARGET_IDS=[2,12,19,81,92]"

$scriptPath = Join-Path $Root $ScriptRel
$probePath = Join-Path $Root $ProbeRel
$sentinelPath = Join-Path $Root $SentinelRel
$runnerPath = Join-Path $Root $RunnerRel

Assert-True (Test-Path $scriptPath) "M1_SCRIPT_EXISTS"

$scriptHashNow = Get-Sha256Lower $scriptPath
$probeHashNow = Get-Sha256Lower $probePath
$sentinelHashNow = Get-Sha256Lower $sentinelPath
$runnerHashNow = Get-Sha256Lower $runnerPath

$scriptLock = ($scriptHashNow -eq [string]$gates.M1_SCRIPT_SHA256_AUDITED)
$probeLock = ($probeHashNow -eq [string]$gates.M1_AUTH_PROBE_SHA256_AUDITED)
$sentinelLock = ($sentinelHashNow -eq [string]$gates.M1_SENTINEL_SHA256_AUDITED)
$runnerLock = ($runnerHashNow -eq [string]$gates.M1_RUNNER_SHA256_AUDITED)
$allHelperLock = $scriptLock -and $probeLock -and $sentinelLock -and $runnerLock

Assert-True $scriptLock "M1_SCRIPT_HASH_LOCK_PASS"
Assert-True $probeLock "M1_AUTH_PROBE_HASH_LOCK_PASS"
Assert-True $sentinelLock "M1_SENTINEL_HASH_LOCK_PASS"
Assert-True $runnerLock "M1_RUNNER_HASH_LOCK_PASS"
Assert-True $allHelperLock "M1_ALL_HELPER_HASH_LOCK_PASS"

$gateCurrent = $true
if ($null -eq $gates.generated_at) { $gateCurrent = $false }
if ($null -eq $gates.M1_SOURCE_AUDIT_GATES_SHA256) { $gateCurrent = $false }
Assert-True $gateCurrent "M1_GATE_FILE_CURRENT"
Assert-True ([bool]$gates.M1_GATE_FILE_SELF_CONSISTENT) "M1_GATE_FILE_SELF_CONSISTENT"

$booleansPass = (
    [bool]$gates.M1_SOURCE_AUDIT_PASS -and
    [bool]$gates.M1_SENTINEL_SOURCE_AUDIT_PASS -and
    [bool]$gates.M1_RUNNER_SOURCE_AUDIT_PASS -and
    [bool]$gates.M1_ALL_EXECUTED_CUSTOM_FILES_AUDITED -and
    [bool]$gates.DB_READ_ONLY_ENFORCED -and
    (-not [bool]$gates.DB_WRITE_PATH_PRESENT) -and
    [bool]$gates.REDIS_READ_ONLY -and
    (-not [bool]$gates.REDIS_MUTATION_PATH_PRESENT) -and
    [bool]$gates.RUBIKA_AUTH_PROBE_READ_ONLY -and
    (-not [bool]$gates.RUBIKA_AUTH_PROBE_PERSISTS_SESSION) -and
    (-not [bool]$gates.RUBIKA_AUTH_PROBE_REQUESTS_OTP) -and
    (-not [bool]$gates.RUBIKA_AUTH_PROBE_WRITES_DB) -and
    (-not [bool]$gates.RUBIKA_AUTH_PROBE_WRITES_REDIS) -and
    (-not [bool]$gates.SESSION_MUTATION_PATH_PRESENT) -and
    (-not [bool]$gates.IDENTITY_MUTATION_PATH_PRESENT) -and
    (-not [bool]$gates.LOGIN_MUTATION_PATH_PRESENT) -and
    (-not [bool]$gates.MESSAGE_MUTATION_PATH_PRESENT) -and
    (-not [bool]$gates.WORKER_MUTATION_PATH_PRESENT) -and
    (-not [bool]$gates.CONFIG_MUTATION_PATH_PRESENT) -and
    (-not [bool]$gates.POOL_MUTATION_PATH_PRESENT) -and
    [bool]$gates.REPORT_WRITE_SCOPE_EXACT -and
    $scriptLock -and $probeLock -and $sentinelLock -and $runnerLock -and $gateCurrent
)

Assert-True $booleansPass "M1_PREEXEC_ALL_GATES_PASS"
Write-Host "MESSAGE_SENT=False"
Write-Host "OTP_REQUESTED=False"
Write-Host "M1_SCRIPT_SHA256_AUDITED=$scriptHashNow"
Write-Host "M1_AUTH_PROBE_SHA256_AUDITED=$probeHashNow"
Write-Host "M1_SENTINEL_SHA256_AUDITED=$sentinelHashNow"
Write-Host "M1_RUNNER_SHA256_AUDITED=$runnerHashNow"

New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

Write-Host "STEP=pre_audit_sentinels"
docker exec -w /app -e PYTHONPATH=/app mmp_core_api python scripts/_m1_pre_audit_sentinels.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "SENTINEL_FAIL_EXIT=$LASTEXITCODE"
    exit $LASTEXITCODE
}
docker exec mmp_core_api test -f /app/reports/rubika-remediation/M1_PRE_AUDIT_SENTINELS.json
if ($LASTEXITCODE -eq 0) {
    docker cp mmp_core_api:/app/reports/rubika-remediation/M1_PRE_AUDIT_SENTINELS.json (Join-Path $ReportDir "M1_PRE_AUDIT_SENTINELS.json")
}
Write-Host "M1_PRE_AUDIT_SENTINELS_CAPTURED=True"

Write-Host "STEP=forensic_audit"
docker exec -w /app -e PYTHONPATH=/app mmp_core_api python scripts/_m1_manual_review_forensic_audit.py
$m1Exit = $LASTEXITCODE
Write-Host "M1_FORENSIC_EXIT=$m1Exit"

$reportFiles = @(
    "M1_MANUAL_REVIEW_FORENSIC_MATRIX.json",
    "M1_MANUAL_REVIEW_FORENSIC_AUDIT.md",
    "M1_MANUAL_REVIEW_REMEDIATION_PLAN.md"
)
foreach ($f in $reportFiles) {
    $remote = "/app/reports/rubika-remediation/$f"
    docker exec mmp_core_api test -f $remote
    if ($LASTEXITCODE -eq 0) {
        docker cp "mmp_core_api:$remote" (Join-Path $ReportDir $f)
        Write-Host "COPIED=$f"
    } else {
        Write-Host "MISSING=$f"
    }
}

Write-Host "M1_RUNNER_EXIT=$m1Exit"
exit $m1Exit
