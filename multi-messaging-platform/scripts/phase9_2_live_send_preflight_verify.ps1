# Phase 9.2 — live send preflight + dry-run (no real delivery unless env flags enabled).
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

Write-Output "Phase 9.2 Live Send Preflight Verification"
Write-Output "Base URL: $BaseUrl"

Write-Section "1. Admin login"
$loginBody = "username=admin&password=admin123"
$login = Invoke-RestMethod -Uri "$BaseUrl/auth/token" -Method POST -ContentType "application/x-www-form-urlencoded" -Body $loginBody
$headers = @{ Authorization = "Bearer $($login.access_token)" }

Write-Section "2. Capabilities (live should be blocked by default)"
$caps = Invoke-RestMethod -Uri "$BaseUrl/accounts/operational-send/capabilities" -Method GET -Headers $headers
$caps | ConvertTo-Json
Assert-True "live_send_allowed=false (default)" ($caps.live_send_allowed -eq $false)
Assert-True "ops_live_send_api_enabled=false (default)" ($caps.ops_live_send_api_enabled -eq $false)

Write-Section "3. Create account + session"
$suffix = Get-Random
$createBody = @{
    platform           = "bale"
    account_identifier = "phase92-bale-$suffix"
    label              = "Phase 9.2"
    status             = "active"
} | ConvertTo-Json
$created = Invoke-RestMethod -Uri "$BaseUrl/accounts" -Method POST -Headers $headers -ContentType $JsonContentType -Body $createBody
$accountId = $created.account_id

Invoke-RestMethod `
    -Uri "$BaseUrl/accounts/$accountId/session/register" `
    -Method POST `
    -Headers $headers `
    -ContentType $JsonContentType `
    -Body (@{ session_payload = "PHASE92_TOKEN" } | ConvertTo-Json) | Out-Null

Write-Section "4. Preflight checklist"
$preflight = Invoke-RestMethod -Uri "$BaseUrl/accounts/$accountId/operational-send/preflight" -Method GET -Headers $headers
$preflight | ConvertTo-Json -Depth 5
Assert-True "preflight ready_for_live_send=false" ($preflight.ready_for_live_send -eq $false)

Write-Section "5. Dry-run send-test"
$send = Invoke-RestMethod `
    -Uri "$BaseUrl/accounts/$accountId/send-test" `
    -Method POST `
    -Headers $headers `
    -ContentType $JsonContentType `
    -Body (@{
        recipient    = "chat-$suffix"
        message_text = "Phase 9.2 dry-run"
        dry_run      = $true
    } | ConvertTo-Json)
Assert-True "dry-run success" ($send.success -eq $true)
Assert-True "status=dry_run" ($send.status -eq "dry_run")

Write-Section "6. Live send blocked without env"
try {
    Invoke-RestMethod `
        -Uri "$BaseUrl/accounts/$accountId/send-test" `
        -Method POST `
        -Headers $headers `
        -ContentType $JsonContentType `
        -Body (@{
            recipient           = "chat-$suffix"
            dry_run             = $false
            confirm_live_send   = $true
        } | ConvertTo-Json) | Out-Null
    Write-Output "FAIL live send should be blocked"
    $Failures++
} catch {
    Assert-True "live send rejected" ($true)
}

Write-Output ""
if ($Failures -eq 0) {
    Write-Output "ALL CHECKS PASSED"
    exit 0
}
Write-Output "FAILED: $Failures check(s)"
exit 1
