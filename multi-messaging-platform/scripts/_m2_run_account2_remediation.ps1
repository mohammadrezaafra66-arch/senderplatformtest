$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$GatePath = Join-Path $Root "reports\rubika-remediation\M2_SOURCE_AUDIT_GATES.json"
$PremPath = Join-Path $Root "reports\rubika-remediation\M2_PREMUTATION_GATES.json"
$ReportDir = Join-Path $Root "reports\rubika-remediation"
$ScriptRel = "scripts\_m2_account2_remediation.py"
$PremRel = "scripts\_m2_premutation_gates.py"
$RunnerRel = "scripts\_m2_run_account2_remediation.ps1"
$HelperRel = "core_engine\services\rubika_legacy_promotion.py"

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

Write-Host "M2_RUNNER_MODE=mutation_only"
Write-Host "M2_RUNNER_NOTE=source_audit_must_already_exist_do_not_regenerate_here"

if (-not (Test-Path $GatePath)) {
    Write-Host "REFUSE: missing M2_SOURCE_AUDIT_GATES.json - regenerate source gates in a separate prior step"
    exit 3
}

$gates = Get-Content -Raw -Path $GatePath | ConvertFrom-Json
Assert-True ([bool]$gates.M2_SOURCE_AUDIT_PASS) "M2_SOURCE_AUDIT_PASS"
Assert-True ([bool]$gates.M2_MUTATION_SCOPE_EXACT) "M2_MUTATION_SCOPE_EXACT"
Assert-True ([bool]$gates.M2_ALL_EXECUTED_CUSTOM_FILES_AUDITED) "M2_ALL_EXECUTED_CUSTOM_FILES_AUDITED"
Assert-True ([bool]$gates.USES_PROMOTE_HELPER) "USES_PROMOTE_HELPER"
Assert-True ([bool]$gates.USES_CLASSIFY_HELPER) "USES_CLASSIFY_HELPER"
Assert-True ([bool]$gates.PREMUTATION_READ_ONLY) "PREMUTATION_READ_ONLY"
Assert-True ([bool]$gates.RUNNER_DOES_NOT_CHAIN_SOURCE_AUDIT) "RUNNER_DOES_NOT_CHAIN_SOURCE_AUDIT"
Assert-False ([bool]$gates.OTP_PATH_PRESENT) "OTP_PATH_PRESENT"
Assert-False ([bool]$gates.MESSAGE_PATH_PRESENT) "MESSAGE_PATH_PRESENT"
Assert-False ([bool]$gates.DB_MIGRATION_PATH_PRESENT) "DB_MIGRATION_PATH_PRESENT"
Assert-False ([bool]$gates.L17_CONFIG_MUTATION_PATH) "L17_CONFIG_MUTATION_PATH"
Assert-False ([bool]$gates.UNRELATED_ACCOUNT_MUTATION_PATH) "UNRELATED_ACCOUNT_MUTATION_PATH"
Assert-False ([bool]$gates.DIRECT_SQL_ACTIVE_PATH) "DIRECT_SQL_ACTIVE_PATH"
Assert-False ([bool]$gates.POOL_CONFIG_MUTATION_PATH) "POOL_CONFIG_MUTATION_PATH"

$scriptPath = Join-Path $Root $ScriptRel
$premPathFile = Join-Path $Root $PremRel
$runnerPath = Join-Path $Root $RunnerRel
$helperPath = Join-Path $Root $HelperRel

$scriptHash = Get-Sha256Lower $scriptPath
$premHash = Get-Sha256Lower $premPathFile
$runnerHash = Get-Sha256Lower $runnerPath
$helperHash = Get-Sha256Lower $helperPath

$scriptLock = ($scriptHash -eq [string]$gates.M2_SCRIPT_SHA256_AUDITED)
$premLock = ($premHash -eq [string]$gates.M2_PREMUTATION_SHA256_AUDITED)
$runnerLock = ($runnerHash -eq [string]$gates.M2_RUNNER_SHA256_AUDITED)
$helperLock = ($helperHash -eq [string]$gates.M2_HELPER_SHA256_AUDITED)

Assert-True $scriptLock "M2_SCRIPT_HASH_LOCK_PASS"
Assert-True $premLock "M2_PREMUTATION_HASH_LOCK_PASS"
Assert-True $runnerLock "M2_RUNNER_HASH_LOCK_PASS"
Assert-True $helperLock "M2_HELPER_HASH_LOCK_PASS"
Assert-True ($scriptLock -and $premLock -and $runnerLock -and $helperLock) "M2_ALL_HELPER_HASH_LOCK_PASS"

Write-Host "M2_SCRIPT_SHA256_AUDITED=$scriptHash"
Write-Host "M2_PREMUTATION_SHA256_AUDITED=$premHash"
Write-Host "M2_RUNNER_SHA256_AUDITED=$runnerHash"
Write-Host "M2_HELPER_SHA256_AUDITED=$helperHash"

New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

Write-Host "STEP=premutation_read_only_gates"
docker exec -w /app -e PYTHONPATH=/app mmp_core_api python scripts/_m2_premutation_gates.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "REFUSE: premutation gates failed exit=$LASTEXITCODE"
    exit $LASTEXITCODE
}
docker exec mmp_core_api test -f /app/reports/rubika-remediation/M2_PREMUTATION_GATES.json
if ($LASTEXITCODE -ne 0) {
    Write-Host "REFUSE: missing M2_PREMUTATION_GATES.json in container"
    exit 3
}
docker cp mmp_core_api:/app/reports/rubika-remediation/M2_PREMUTATION_GATES.json $PremPath

$prem = Get-Content -Raw -Path $PremPath | ConvertFrom-Json
Assert-True ([bool]$prem.M2_PREMUTATION_GATES_PASS) "M2_PREMUTATION_GATES_PASS"
Assert-True ([bool]$prem.SESSION721_FRESH_PROOF_PASS) "SESSION721_FRESH_PROOF_PASS"
Assert-True ([bool]$prem.SESSION721_AUTH_PASS) "SESSION721_AUTH_PASS"
Assert-True ([bool]$prem.SESSION721_IDENTITY_MATCH) "SESSION721_IDENTITY_MATCH"
Assert-True ([bool]$prem.SESSION2_DECRYPT_FAILED_CONFIRMED) "SESSION2_DECRYPT_FAILED_CONFIRMED"
Assert-True ([bool]$prem.NO_ACTIVE_OTP) "NO_ACTIVE_OTP"
Assert-True ([bool]$prem.NO_QUEUE_ACTIVITY) "NO_QUEUE_ACTIVITY"
Assert-True ([bool]$prem.BASELINE_ACTIVES_OK) "BASELINE_ACTIVES_OK"
Assert-True ([bool]$prem.BASELINE_WORKERS_OK) "BASELINE_WORKERS_OK"
Assert-True ([bool]$prem.ACCOUNT2_ROLLBACK_READY) "ACCOUNT2_ROLLBACK_READY"

$resumePartial = [bool]$prem.M2_RESUME_PARTIAL
$freshPath = [bool]$prem.M2_FRESH_PATH
if ($resumePartial) {
    Write-Host "M2_EXECUTION_MODE=resume_partial"
    Assert-True $resumePartial "M2_RESUME_PARTIAL"
    Assert-True ([bool]$prem.ACCOUNT2_SOLE_ACTIVE_721) "ACCOUNT2_SOLE_ACTIVE_721"
    Assert-False ([bool]$prem.NO_ACCOUNT2_ACTIVE_SESSION) "NO_ACCOUNT2_ACTIVE_SESSION_EXPECTED_FALSE_ON_RESUME"
} elseif ($freshPath) {
    Write-Host "M2_EXECUTION_MODE=fresh"
    Assert-True ([bool]$prem.NO_ACCOUNT2_ACTIVE_SESSION) "NO_ACCOUNT2_ACTIVE_SESSION"
    Assert-True $freshPath "M2_FRESH_PATH"
} else {
    Write-Host "REFUSE: neither M2_FRESH_PATH nor M2_RESUME_PARTIAL"
    exit 3
}

docker exec mmp_core_api test -f /app/reports/rubika-remediation/M2_ACCOUNT2_ROLLBACK_MANIFEST.json
if ($LASTEXITCODE -eq 0) {
    docker cp mmp_core_api:/app/reports/rubika-remediation/M2_ACCOUNT2_ROLLBACK_MANIFEST.json (Join-Path $ReportDir "M2_ACCOUNT2_ROLLBACK_MANIFEST.json")
    Write-Host "COPIED=M2_ACCOUNT2_ROLLBACK_MANIFEST.json"
}

$pathGate = ($resumePartial -and [bool]$prem.ACCOUNT2_SOLE_ACTIVE_721) -or ($freshPath -and [bool]$prem.NO_ACCOUNT2_ACTIVE_SESSION)
Write-Host "M2_PATH_GATE=$pathGate"
Assert-True $pathGate "M2_PATH_GATE"

$preexec = (
    [bool]$gates.M2_SOURCE_AUDIT_PASS -and
    [bool]$gates.M2_MUTATION_SCOPE_EXACT -and
    $scriptLock -and $premLock -and $runnerLock -and $helperLock -and
    [bool]$prem.M2_PREMUTATION_GATES_PASS -and
    [bool]$prem.SESSION721_FRESH_PROOF_PASS -and
    [bool]$prem.SESSION721_AUTH_PASS -and
    [bool]$prem.SESSION721_IDENTITY_MATCH -and
    [bool]$prem.SESSION2_DECRYPT_FAILED_CONFIRMED -and
    $pathGate -and
    [bool]$prem.NO_ACTIVE_OTP -and
    [bool]$prem.NO_QUEUE_ACTIVITY -and
    [bool]$prem.ACCOUNT2_ROLLBACK_READY -and
    (-not [bool]$gates.OTP_PATH_PRESENT) -and
    (-not [bool]$gates.MESSAGE_PATH_PRESENT) -and
    (-not [bool]$gates.L17_CONFIG_MUTATION_PATH) -and
    (-not [bool]$gates.UNRELATED_ACCOUNT_MUTATION_PATH)
)

Write-Host "TARGET_ACCOUNT_ID=2"
Write-Host "PROVEN_AUTHORITATIVE_SESSION=721"
Write-Host "INVALID_SESSION=2"
Write-Host "OTP_REQUESTED=False"
Write-Host "MESSAGE_SENT=False"
Assert-True $preexec "M2_PREEXEC_ALL_GATES_PASS"

Write-Host "STEP=m2_account2_remediation_mutation"
docker exec -w /app -e PYTHONPATH=/app -e M2_ALLOW_ACCOUNT2_REMEDIATION=1 -e M2_PRODUCTION_TARGET=account2 mmp_core_api python scripts/_m2_account2_remediation.py
$m2Exit = $LASTEXITCODE
Write-Host "M2_REMEDIATION_EXIT=$m2Exit"

$reportFiles = @(
    "M2_ACCOUNT2_REMEDIATION_RESULT.json",
    "M2_ACCOUNT2_REMEDIATION_AUDIT.md",
    "M2_ACCOUNT2_ROLLBACK_MANIFEST.json"
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

Write-Host "M2_RUNNER_EXIT=$m2Exit"
exit $m2Exit
