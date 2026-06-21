# Phase 8.6 — session wiring + deploy readiness verification (API only, no live send).
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

Write-Output "Phase 8.6 Session Wiring + Deploy Readiness"
Write-Output "Base URL: $BaseUrl"

# Login (dev default admin — adjust if your auth differs)
Write-Section "1. Admin login"
$loginBody = @{ username = "admin"; password = "admin" } | ConvertTo-Json
try {
    $login = Invoke-RestMethod -Uri "$BaseUrl/auth/login" -Method POST -ContentType $JsonContentType -Body $loginBody
    $token = $login.access_token
    $headers = @{ Authorization = "Bearer $token" }
    Assert-True "admin login" ($null -ne $token)
} catch {
    Write-Output "SKIP login (use MMP_AUTH_TOKEN env or ensure admin/admin exists): $_"
    if ($env:MMP_AUTH_TOKEN) {
        $headers = @{ Authorization = "Bearer $env:MMP_AUTH_TOKEN" }
    } else {
        throw "Cannot authenticate. Set MMP_AUTH_TOKEN or ensure API auth is available."
    }
}

Write-Section "2. Deploy readiness"
$readiness = Invoke-RestMethod -Uri "$BaseUrl/accounts/deploy/readiness" -Method GET -Headers $headers
$readiness | ConvertTo-Json -Depth 10
Assert-True "phase=9.1" ($readiness.phase -eq "9.1")
Assert-True "worker_services listed" ($readiness.worker_services.Count -ge 4)
Assert-True "safety payload present" ($null -ne $readiness.safety)

Write-Section "3. Safety gates (default dry-run safe)"
Assert-True "real_message_sending disabled" ($readiness.safety.real_message_sending_enabled -eq $false)
Assert-True "channel_connectors disabled" ($readiness.safety.channel_connectors_enabled -eq $false)

Write-Section "4. Create Bale test account + register session"
$baleBody = @{
    platform           = "bale"
    account_identifier = "phase8-bale-$(Get-Random)"
    label              = "Phase 8.6 Bale"
    status             = "requires_login"
} | ConvertTo-Json
$created = Invoke-RestMethod -Uri "$BaseUrl/accounts" -Method POST -Headers $headers -ContentType $JsonContentType -Body $baleBody
$baleId = $created.account_id
Assert-True "bale account created" ($baleId -gt 0)

$statusBefore = Invoke-RestMethod -Uri "$BaseUrl/accounts/$baleId/session/status" -Method GET -Headers $headers
Assert-True "session not ready before register" ($statusBefore.ready_for_delivery -eq $false)

$registerBody = @{ session_payload = "PHASE8_TEST_BALE_TOKEN" } | ConvertTo-Json
$registered = Invoke-RestMethod `
    -Uri "$BaseUrl/accounts/$baleId/session/register" `
    -Method POST `
    -Headers $headers `
    -ContentType $JsonContentType `
    -Body $registerBody
Assert-True "session registered" ($registered.success -eq $true)

$statusAfter = Invoke-RestMethod -Uri "$BaseUrl/accounts/$baleId/session/status" -Method GET -Headers $headers
Assert-True "session ready after register" ($statusAfter.ready_for_delivery -eq $true)
Assert-True "session_registered=true" ($statusAfter.session_registered -eq $true)

Write-Section "5. Test connection"
$test = Invoke-RestMethod -Uri "$BaseUrl/accounts/$baleId/test-connection" -Method POST -Headers $headers -ContentType $JsonContentType -Body "{}"
Assert-True "test-connection success" ($test.success -eq $true)

Write-Output ""
if ($Failures -eq 0) {
    Write-Output "ALL CHECKS PASSED ($Failures failures)"
    exit 0
}
Write-Output "FAILED: $Failures check(s)"
exit 1
