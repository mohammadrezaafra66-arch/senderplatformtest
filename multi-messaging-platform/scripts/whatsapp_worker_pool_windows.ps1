# Run WhatsApp worker pool on Windows host (Phase 1 dev).
# Uses the local Chrome profile from whatsapp_web_link_local.ps1 — NOT the Docker copy.
#
# Prerequisites:
#   - Docker: postgres, redis, core_api running (whatsapp_worker_pool STOPPED)
#   - WhatsApp linked locally with chats visible at least once
#   - .env has WHATSAPP_OPS_SEND_VIA_WORKER_QUEUE=true for UI live send via queue
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\whatsapp_worker_pool_windows.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Stopping Docker whatsapp_worker_pool (host worker replaces it) ..."
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
docker compose stop whatsapp_worker_pool 2>&1 | Out-Null
$ErrorActionPreference = $prevEap

$venvPython = Join-Path $Root ".playwright-venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "Missing .playwright-venv — run whatsapp_web_link_local.ps1 once first."
}

$localProfileRoot = Join-Path $env:LOCALAPPDATA "SenderPlatform\mmp-whatsapp"
if (-not (Test-Path (Join-Path $localProfileRoot "account-248\Default"))) {
    Write-Host "WARNING: Local profile for account-248 not found under $localProfileRoot" -ForegroundColor DarkYellow
    Write-Host "Run: powershell -ExecutionPolicy Bypass -File .\scripts\whatsapp_web_link_local.ps1 -AccountId 248" -ForegroundColor DarkYellow
}

$env:DATABASE_URL = "postgresql://mmp_user:mmp_pass@127.0.0.1:5433/mmp_db"
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
$env:WORKER_PLATFORM = "whatsapp"
$env:WHATSAPP_DELIVERY_MODE = "web"
$env:WHATSAPP_ACCOUNT_IDS = "248"
$env:WORKER_POOL_SIZE = "1"
$env:WORKER_POOL_INDEX = "0"
$env:WORKER_POLL_INTERVAL_SECONDS = "5"
$env:WHATSAPP_WEB_PROFILE_ROOT = $localProfileRoot
$env:WHATSAPP_WEB_HEADLESS = "false"
$env:DRY_RUN = "false"
$env:REAL_MESSAGE_SENDING_ENABLED = "true"
$env:CHANNEL_CONNECTORS_ENABLED = "true"
$env:WORKER_EXECUTION_ENABLED = "true"

Write-Host ""
Write-Host "=== WhatsApp worker pool (Windows host) ===" -ForegroundColor Cyan
Write-Host "Profile root: $localProfileRoot" -ForegroundColor Gray
Write-Host "Redis:        $env:REDIS_URL" -ForegroundColor Gray
Write-Host "Postgres:     $env:DATABASE_URL" -ForegroundColor Gray
Write-Host ""
Write-Host "Keep this window open. Send from UI (live) or queue_bridge." -ForegroundColor Yellow
Write-Host "Restart core_api after .env change: docker compose restart core_api" -ForegroundColor DarkYellow
Write-Host ""

& $venvPython -m workers.run_pool_forever
