# Phase 9.1 — operational E2E verification (dry-run send by default; no live delivery).
$ErrorActionPreference = "Stop"
$BaseUrl = if ($env:MMP_BASE_URL) { $env:MMP_BASE_URL } else { "http://localhost:8001" }
$JsonContentType = "application/json; charset=utf-8"

function Write-Section($title) {
    Write-Output ""
    Write-Output "=== $title ==="
}

function Assert-True($label, $condition) {
    if ($condition) {
        Write-Output "OK   $label"
    } else {
        Write-Output "FAIL $label"
        $script:Failures++
    }
}

$Failures = 0

Write-Output "Phase 9.1 Operational E2E (dry-run)"
Write-Output "Base URL: $BaseUrl"

Write-Section "1. Admin login"
$loginBody = "username=admin&password=admin123"
try {
    $login = Invoke-RestMethod -Uri "$BaseUrl/auth/token" -Method POST -ContentType "application/x-www-form-urlencoded" -Body $loginBody
    $token = $login.access_token
    $headers = @{ Authorization = "Bearer $token" }
    Assert-True "admin login" ($null -ne $token)
} catch {
    if ($env:MMP_AUTH_TOKEN) {
        $headers = @{ Authorization = "Bearer $env:MMP_AUTH_TOKEN" }
        Write-Output "Using MMP_AUTH_TOKEN"
    } else {
        throw "Cannot authenticate: $_"
    }
}

Write-Section "2. Deploy readiness (phase 9.1)"
$readiness = Invoke-RestMethod -Uri "$BaseUrl/accounts/deploy/readiness" -Method GET -Headers $headers
$readiness | ConvertTo-Json -Depth 10
Assert-True "phase=9.2" ($readiness.phase -eq "9.2")
Assert-True "operational_send block present" ($null -ne $readiness.operational_send)

Write-Section "3. Create account + session + dry-run send-test"
$suffix = Get-Random
$createBody = @{
    platform           = "telegram"
    account_identifier = "@ops_test_$suffix"
    label              = "Phase 9.1 Ops"
    status             = "requires_login"
} | ConvertTo-Json
$created = Invoke-RestMethod -Uri "$BaseUrl/accounts" -Method POST -Headers $headers -ContentType $JsonContentType -Body $createBody
$accountId = $created.account_id
Assert-True "account created" ($accountId -gt 0)

Invoke-RestMethod `
    -Uri "$BaseUrl/accounts/$accountId/session/register" `
    -Method POST `
    -Headers $headers `
    -ContentType $JsonContentType `
    -Body (@{ session_payload = "OPS_TEST_TOKEN_$suffix" } | ConvertTo-Json) | Out-Null

$sendBody = @{
    recipient    = "@test_user_$suffix"
    message_text = "Phase 9.1 dry-run operational test"
    dry_run      = $true
} | ConvertTo-Json
$send = Invoke-RestMethod `
    -Uri "$BaseUrl/accounts/$accountId/send-test" `
    -Method POST `
    -Headers $headers `
    -ContentType $JsonContentType `
    -Body $sendBody
$send | ConvertTo-Json -Depth 5
Assert-True "dry-run send success" ($send.success -eq $true)
Assert-True "status=dry_run" ($send.status -eq "dry_run")

Write-Output ""
if ($Failures -eq 0) {
    Write-Output "ALL CHECKS PASSED"
    exit 0
}
Write-Output "FAILED: $Failures check(s)"
exit 1
